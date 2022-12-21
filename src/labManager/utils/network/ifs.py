import psutil
import ipaddress
import socket

def get_ifaces(ip_network='192.0.2.0/28'):
    network = ipaddress.IPv4Network(ip_network)

    matches = []
    for iface in (ifs:=psutil.net_if_addrs()):
        for addr in ifs[iface]:
            if addr.family!=socket.AF_INET:
                continue
            if ipaddress.IPv4Address(addr.address) not in network:
                continue
            matches.append(addr.address)

    return sorted(matches)
