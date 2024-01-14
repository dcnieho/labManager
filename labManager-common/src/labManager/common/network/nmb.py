# async implementation of pysmb's nmb module
# periodically sends out a query for NetBIOS netnames to a range of IPs
# collects answers, and removes those older than a specific age
import asyncio
import ipaddress
from nmb.base import NBNS   # from pysmb package
from nmb.nmb_constants import TYPE_SERVER
import random
import pathlib
import time
import threading
from dataclasses import dataclass

from . import ifs

@dataclass
class _Machine:
    name: str
    ip: ipaddress.IPv4Address
    last_seen: float

class NetBIOSQuerierProtocol(asyncio.DatagramProtocol):
    def __init__(self, machines: list[_Machine], machines_lock: threading.Lock):
        self.done = asyncio.get_running_loop().create_future()
        self.machines = machines
        self.machines_lock = machines_lock
        self.packet_logic = NBNS()

    def connection_made(self, _: asyncio.DatagramTransport):
        pass

    def datagram_received(self, data, addr: tuple[str, int]):
        t = time.monotonic()
        ip = ipaddress.IPv4Address(addr[0])
        _, ret = self.packet_logic.decodeIPQueryPacket(data)
        if ret is None:
            return
        names = [s[0] for s in ret if s[1]==TYPE_SERVER]
        if not names:
            return  # not an name-from-IP query reply
        name = names[0]
        with self.machines_lock:
            found = False
            for m in self.machines:
                if m.name==name or m.ip==ip:
                    # update entry
                    m.name = name
                    m.ip   = ip
                    m.last_seen = t
                    # done
                    found = True
                    break
            if not found:
                self.machines.append(_Machine(name,ip,t))

    def error_received(self, _: Exception):
        pass    # doesn't matter if hosts are not online or close the connection (don't respond to the query)

    def connection_lost(self, exc: Exception|None):
        if self.done:
            self.done.set_result(None)

    def prepare_query(self):
        trn_id = random.randint(1, 0xFFFF)
        return self.packet_logic.prepareNetNameQuery(trn_id, False)


class NetBIOSDiscovery:
    def __init__(self, ip_network: str|ipaddress.IPv4Network, interval: float, age_out: float = None):
        # by default, items age out when they are older than 2x interval
        network = ipaddress.IPv4Network(ip_network)
        if network.num_addresses > 512:
            raise ValueError(f'Too many addresses in network {ip_network}')

        self.network            : ipaddress.IPv4Network     = network
        self.interval           : float                     = interval
        self.age_out            : float                     = age_out
        if not age_out:
            self.age_out = 2*self.interval

        self._machines          : list[_Machine]            = []
        self._machines_lock     : threading.Lock            = threading.Lock()

        self._doing_discovery   : bool                      = False
        self._discovery_task    : asyncio.Task              = None
        self._loop              : asyncio.AbstractEventLoop = None
        self._transport         : asyncio.DatagramTransport = None
        self._protocol          : NetBIOSQuerierProtocol    = None
        self._waiter            : asyncio.Event             = asyncio.Event()

    def __del__(self):
        self._stop()
        self._stop2()

    async def start(self):
        # get if on configured network that we'll open the socket on
        if_ips, _ = ifs.get_ifaces(self.network)
        if not if_ips:
            raise RuntimeError(f'No interfaces found that are connected to the configured network {self.network}')
        # start transport
        self._loop = asyncio.get_running_loop()
        self._transport, self._protocol = \
            await self._loop.create_datagram_endpoint(
                lambda: NetBIOSQuerierProtocol(self._machines, self._machines_lock),
                local_addr=(if_ips[0],0))

        # start discovery loop
        self._doing_discovery = True
        self._discovery_task = asyncio.create_task(self._discovery_loop())
        return self._discovery_task

    async def _discovery_loop(self):
        while self._doing_discovery:
            # serially send name requests to potential hosts, is fast enough, and no asyncio.Task overhead from asyncio.gather and co
            for h in self.network.hosts():
                if not self._transport.is_closing():
                    self._transport.sendto(self._protocol.prepare_query(), (str(h), 137))

            # lastly, wait for interval or until asyncio.Event is set
            try:
                await asyncio.wait_for(self._waiter.wait(), self.interval)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            self._waiter.clear()

    def is_running(self):
        if self._doing_discovery:
            if self._transport.is_closing() or self._protocol.done.done():
                self._doing_discovery = False
        return self._doing_discovery

    async def do_discover_now(self):
        # kick discovery loop into action
        self._waiter.set()

    async def stop(self):
        self._stop()
        # wait for it to stop
        await asyncio.sleep(0)
        self._stop2()
        # wait till closed
        await asyncio.wait([self._protocol.done, self._discovery_task], return_when=asyncio.FIRST_COMPLETED)
    def _stop(self):
        self._doing_discovery = False
        self._transport.close()
        # kick discovery loop into action
        self._waiter.set()
    def _stop2(self):
        if self._discovery_task and not self._discovery_task.done():
            self._discovery_task.cancel()

    def get_machines(self, as_direntry = False):
        t = time.monotonic()
        with self._machines_lock:
            machines = [(m.name, m.ip) for m in self._machines if t-m.last_seen<self.age_out]
        if as_direntry:
            from .. import structs
            # NB: //SERVER/ is the format pathlib understands and can concatenate share names to
            machines = [(structs.DirEntry(m,True,pathlib.Path(f'//{m}/'),None,None,None,'labManager/net_name'), ip) for m,ip in machines]
        return machines