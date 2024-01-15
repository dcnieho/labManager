# async implementation of pysmb's nmb module
# periodically sends out a query for NetBIOS netnames to a range of IPs
# collects answers, and removes those older than a specific age
import asyncio
import ipaddress
from nmb.base import NBNS   # from pysmb package
from nmb.nmb_constants import TYPE_SERVER
import random
import pathlib
import socket
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
    def __init__(self, queue: asyncio.Queue):
        self.queue: asyncio.Queue = queue

    def datagram_received(self, data, addr: tuple[str, int]):
        t = time.monotonic()
        ip = ipaddress.IPv4Address(addr[0])
        self.queue.put_nowait((data, ip, t))

    def error_received(self, exc: Exception):
        self.queue.put_nowait((exc, None, None))



class NetBIOSDiscovery:
    def __init__(self, ip_network: str|ipaddress.IPv4Network, interval: float, age_out: float = None):
        # by default, items age out when they are older than 2x interval
        network = ipaddress.IPv4Network(ip_network)
        if network.num_addresses > 512:
            raise ValueError(f'Too many addresses in network {ip_network}')

        self.network            : ipaddress.IPv4Network     = network
        self._if_ip             : str                       = None
        self.interval           : float                     = interval
        self.age_out            : float                     = age_out
        if not age_out:
            self.age_out = 2*self.interval

        self._response_queue    : asyncio.Queue             = asyncio.Queue()
        self._machines          : list[_Machine]            = []
        self._machines_lock     : threading.Lock            = threading.Lock()

        self._tasks             : set[asyncio.Task]         = set()
        
        self._packet_logic      : NBNS                      = NBNS()

        self._discovery_task    : asyncio.Task              = None
        self._transport         : asyncio.DatagramTransport = None
        self._waiter            : asyncio.Event             = asyncio.Event()

    async def run(self):
        # get if on configured network that we'll open the socket on
        if_ips, _ = ifs.get_ifaces(self.network)
        if not if_ips:
            raise RuntimeError(f'No interfaces found that are connected to the configured network {self.network}')
        self._if_ip = if_ips[0]

        # start transport
        await self._make_transport()

        # start discovery loop and response receiver
        self._tasks.add(asyncio.create_task(self._discovery_loop()))
        self._tasks.add(asyncio.create_task(self._queue_reader()))

        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            for t in self._tasks:
                t.cancel()
            await asyncio.wait(self._tasks)
            return
        finally:
            if self._transport and not self._transport.is_closing():
                self._transport.close()

    async def _make_transport(self):
        if self._transport is not None:
            try:
                self._transport.close()
            except OSError:
                pass

        self._transport, _ = \
            await asyncio.get_running_loop().create_datagram_endpoint(
                lambda: NetBIOSQuerierProtocol(self._response_queue),
                local_addr=(self._if_ip,0))

    async def _discovery_loop(self):
        while True:
            # serially send name requests to potential hosts, is fast enough, and no asyncio.Task overhead from asyncio.gather and co
            for h in self.network.hosts():
                if not self._transport.is_closing():
                    data = self._prepare_query()
                    self._transport.sendto(data, (str(h), 137))

            # lastly, wait for interval or until asyncio.Event is set
            try:
                await asyncio.wait_for(self._waiter.wait(), self.interval)
            except asyncio.TimeoutError:
                pass
            self._waiter.clear()
        
    async def _queue_reader(self):
        while True:
            data, ip, t = await self._response_queue.get()
            if isinstance(data, OSError) or isinstance(data, ConnectionResetError):
                # e.g error 1234: No service is operating at the destination network endpoint on the remote system when you get ICMP
                # ConnectionResetError: On a UDP-datagram socket this error indicates a previous send operation resulted in an ICMP Port Unreachable message.
                # this causes the transport to not read any new data (https://github.com/python/cpython/issues/91227, https://github.com/python/cpython/issues/88906)
                # recreate transport and hope for the best
                # solution similar to https://github.com/caproto/caproto/commit/17e55d3447180dbddc23b88171e7a95bc9c08d4a and https://github.com/caproto/caproto/commit/fefba96b43c944e4cfef98eb499e6a3d09473df8
                # So recreate the socket here and hope for the best:
                await self._make_transport()
            else:
                _, ret = self._packet_logic.decodeIPQueryPacket(data)
                if ret is None:
                    return
                names = [s[0] for s in ret if s[1]==TYPE_SERVER]
                if not names:
                    return  # not an name-from-IP query reply
                name = names[0]
                with self._machines_lock:
                    found = False
                    for m in self._machines:
                        if m.name==name or m.ip==ip:
                            # update entry
                            m.name = name
                            m.ip   = ip
                            m.last_seen = t
                            # done
                            found = True
                            break
                    if not found:
                        self._machines.append(_Machine(name,ip,t))

    def _prepare_query(self):
        trn_id = random.randint(1, 0xFFFF)
        return self._packet_logic.prepareNetNameQuery(trn_id, False)

    async def do_discover_now(self):
        # kick discovery loop into action
        self._waiter.set()

    def get_machines(self, as_direntry = False):
        t = time.monotonic()
        with self._machines_lock:
            machines = [(m.name, m.ip) for m in self._machines if t-m.last_seen<self.age_out]

        if as_direntry:
            from .. import structs
            # NB: //SERVER/ is the format pathlib understands and can concatenate share names to
            machines = [(structs.DirEntry(m,True,pathlib.Path(f'//{m}/'),None,None,None,'labManager/net_name'), ip) for m,ip in machines]
        return machines