import sys
import ipaddress
import socket

def get_ifaces(ip_network):
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
    ips, macs = zip(*[(x,y) for x,y in sorted(zip(ips, macs))])
    return ips, macs


# https://stackoverflow.com/a/41420850
def _getNics() :
    from subprocess import check_output
    from xml.etree.ElementTree import fromstring
    from ipaddress import IPv4Interface, IPv6Interface

    cmd = 'wmic.exe nicconfig where "IPEnabled = True" get ipaddress,MACAddress,IPSubnet /format:rawxml'
    xml_text = check_output(cmd, creationflags=8)
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