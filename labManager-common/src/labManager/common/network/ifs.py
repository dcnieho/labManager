import sys
import ipaddress
import socket

def get_ifaces(ip_network: str):
    network = ipaddress.IPv4Network(ip_network)

    macs= []
    ips = []
    if sys.platform.startswith('win'):
        for iface in (ifs:=_getNics()):
            mac = None
            ip  = None
            iface['ip'] = [ip for ip in iface['ip'] if isinstance(ip,ipaddress.IPv4Address)][0]    # keep only IPv4
            if iface['mac'] and iface['ip'] in network:
                macs.append(iface['mac'])
                ips .append(str(iface['ip'].ip))
    else:
        import psutil
        for iface in (ifs:=psutil.net_if_addrs()):
            mac = None
            ip  = None
            for addr in ifs[iface]:
                if addr.family==psutil.AF_LINK:
                    mac= addr.address
                if addr.family==socket.AF_INET:
                    ip = addr.address
            if mac and ip and ipaddress.IPv4Address(ip) in network:
                macs.append(mac)
                ips .append(ip)

    if not ips:
        return [],[]

    # sort both based on ip, return
    # NB: special key turns ips into integer tuple so that lexicographical sort does the right thing
    ips, macs = zip(*[(x,y) for x,y in sorted(zip(ips, macs), key=lambda item: (*socket.inet_aton(item[0]), item[1]))])
    return ips, macs

def _getNics():
    for method in (_getNicsWmic, _getNicsPwsh, _getNicsIpconfig):
        try:
            nics = method()
            if nics:
                return nics
        except Exception as e:
            # Optionally log or print the error for debugging
            # print(f"{method.__name__} failed: {e}")
            continue
    return []  # If all methods fail

# https://stackoverflow.com/a/41420850
def _getNicsWmic():
    import subprocess
    from xml.etree.ElementTree import fromstring
    from ipaddress import IPv4Interface, IPv6Interface

    cmd = 'wmic.exe nicconfig where "IPEnabled = True" get ipaddress,MACAddress,IPSubnet /format:rawxml'
    xml_text = subprocess.check_output(cmd, creationflags=8)
    xml_root = fromstring(xml_text)

    nics = []
    keyslookup = {
        'IPAddress' : 'ip',
        'IPSubnet' : '_mask',
        'MACAddress' : 'mac',
    }

    for nic in xml_root.findall("./RESULTS/CIM/INSTANCE") :
        # parse and store nic info
        n = {
            'ip':[],
            '_mask':[],
            'mac':'',
        }
        for prop in nic :
            name = keyslookup[prop.attrib['NAME']]
            if prop.tag == 'PROPERTY':
                if len(prop):
                    for v in prop:
                        n[name] = v.text
            elif prop.tag == 'PROPERTY.ARRAY':
                for v in prop.findall("./VALUE.ARRAY/VALUE") :
                    n[name].append(v.text)
        nics.append(n)

        # creates python ipaddress objects from ips and masks
        for i in range(len(n['ip'])) :
            arg = '%s/%s'%(n['ip'][i],n['_mask'][i])
            if ':' in n['ip'][i]:
                n['ip'][i] = IPv6Interface(arg)
            else:
                n['ip'][i] = IPv4Interface(arg)
        del n['_mask']

    return nics

def _getNicsPwsh() :
    import subprocess
    import json
    from ipaddress import IPv4Interface, IPv6Interface

    cmd     = 'where powershell'
    path    = subprocess.check_output(cmd, creationflags=8)
    cmd     = [path.strip(),
               '-NoProfile',
               '-Command',
               'Get-CimInstance -ClassName Win32_NetworkAdapterConfiguration -Filter "IPEnabled = True" | ',
               'Select-Object IPAddress, IPSubnet, MACAddress | ',
               'ConvertTo-Json -Compress']
    ns      = json.loads(subprocess.check_output(cmd, creationflags=8))
    if isinstance(ns, dict):
        ns = [ns]
    nics = []
    for n in ns:
        ips = [(ip,f'{ip}/{mask}') for ip,mask in zip(n["IPAddress"],n["IPSubnet"])]
        ips = [IPv6Interface(arg[1]) if ':' in arg[0] else IPv4Interface(arg[1]) for arg in ips]
        nics.append({'ip': ips, 'mac': n["MACAddress"]})

    return nics

def _getNicsIpconfig():
    # NB: english Windows only, not localized
    import subprocess
    import re
    from ipaddress import IPv4Interface

    output = subprocess.check_output("ipconfig /all", creationflags=8).decode("utf-8", errors="ignore")

    nics = []
    current_nic = None
    current_ip = None

    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue

        # Start of a new NIC block
        if re.match(r"^[^\s].*adapter", line, re.IGNORECASE):
            if current_nic and current_nic['ip'] and current_nic['mac']:
                nics.append(current_nic)
            current_nic = {'ip': [], 'mac': None}
            current_ip = None
            continue

        if current_nic is None:
            continue

        # MAC Address
        mac_match = re.search(r"Physical Address.*?:\s+([-\w]+)", line)
        if mac_match:
            current_nic['mac'] = mac_match.group(1)

        # IPv4 Address
        ipv4_match = re.search(r"IPv4 Address.*?:\s+([\d\.]+)", line)
        if ipv4_match:
            current_ip = {'ip': ipv4_match.group(1), 'mask': None}

        # Subnet Mask
        mask_match = re.search(r"Subnet Mask.*?:\s+([\d\.]+)", line)
        if mask_match and current_ip:
            ip_with_mask = f"{current_ip['ip']}/{mask_match.group(1)}"
            current_nic['ip'].append(IPv4Interface(ip_with_mask))
            current_ip = None  # Reset for next IP

    # Add last NIC if valid
    if current_nic and current_nic['ip'] and current_nic['mac']:
        nics.append(current_nic)

    return nics