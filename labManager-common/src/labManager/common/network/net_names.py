import ipaddress
import asyncio
from ..impacket import nmb
from icmplib import async_multiping

async def get_network_computers(ip_network):
    network = ipaddress.IPv4Network(ip_network)
    if network.num_addresses > 512:
        raise ValueError(f'Too many addresses in network {ip_network}')

    # first see what potential hosts are up
    hosts = await async_multiping([str(h) for h in network.hosts()], count=1, timeout=1)

    # then get their names
    names = {}
    for h in (h for h in hosts if h.is_alive):
        N = nmb.NetBIOS()
        try:
            name = N.getnetbiosname(h.address, timeout=0.2, tries=2)
            if name not in names:
                names[name] = h.address
        except nmb.NetBIOSTimeout:
            pass
        await asyncio.sleep(0)    # don't block

    return names