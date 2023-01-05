import psutil
import ipaddress
import socket

def get_ifaces(ip_network):
    network = ipaddress.IPv4Network(ip_network)

    macs= []
    ips = []
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
