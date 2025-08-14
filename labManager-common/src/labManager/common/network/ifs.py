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

def _getNics() :
    import subprocess
    import json
    from ipaddress import IPv4Interface, IPv6Interface

    cmd     = 'where powershell'
    path    = subprocess.check_output(cmd)
    cmd     = [path.strip(), 'Get-CimInstance -ClassName Win32_NetworkAdapterConfiguration -Filter "IPEnabled = True" | select IPAddress,IPSubnet,MACAddress | ConvertTo-JSON']
    ns      = json.loads(subprocess.check_output(cmd))
    nics = []
    for n in ns:
        ips = [(ip,f'{ip}/{mask}') for ip,mask in zip(n["IPAddress"],n["IPSubnet"])]
        ips = [IPv6Interface(arg[1]) if ':' in arg[0] else IPv4Interface(arg[1]) for arg in ips]
        nics.append({'ip': ips, 'mac': n["MACAddress"]})

    return nics