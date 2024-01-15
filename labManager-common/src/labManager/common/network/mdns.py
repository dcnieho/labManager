import asyncio
import inspect
import ipaddress
from typing import Any, Optional, cast

from zeroconf import ServiceStateChange, Zeroconf
from zeroconf.asyncio import (
    AsyncServiceBrowser,
    AsyncServiceInfo,
    AsyncZeroconf,
)

from . import ifs
from .. import async_thread


class Announcer:
    def __init__(self, ip_network: str|ipaddress.IPv4Network, service: str, name: str, address: tuple[str,int]):
        network = ipaddress.IPv4Network(ip_network)
        if network.num_addresses > 512:
            raise ValueError(f'Too many addresses in network {ip_network}')
        self.network            : ipaddress.IPv4Network     = network
        self._if_ip             : str                       = None

        self.info = AsyncServiceInfo(
                service,
                f'{name}.{service}',
                parsed_addresses=[address[0]],
                port=address[1],
            )
        self.aiozc              : Optional[AsyncZeroconf]       = None

    async def run(self):
        # get if on configured network that we'll open the socket on
        if_ips, _ = ifs.get_ifaces(self.network)
        if not if_ips:
            raise RuntimeError(f'No interfaces found that are connected to the configured network {self.network}')
        self._if_ip = if_ips[0]

        self.aiozc = AsyncZeroconf(interfaces=[self._if_ip])
        await self.aiozc.zeroconf.async_wait_for_start()

        # start announce
        tasks = [self.aiozc.async_register_service(self.info)]
        background_tasks = await asyncio.gather(*tasks)
        try:
            await asyncio.gather(*background_tasks)
        except asyncio.CancelledError:
            return  # cancellation processed
        finally:
            # cleanup
            tasks = [self.aiozc.async_unregister_service(self.info)]
            background_tasks = await asyncio.gather(*tasks)
            await asyncio.gather(*background_tasks)
            await self.aiozc.async_close()


class Discoverer:
    def __init__(self, ip_network: str|ipaddress.IPv4Network, service: str, wanted_name: str, response_handler = None):
        network = ipaddress.IPv4Network(ip_network)
        if network.num_addresses > 512:
            raise ValueError(f'Too many addresses in network {ip_network}')
        self.network            : ipaddress.IPv4Network     = network
        self._if_ip             : str                       = None

        self.service            : str                       = service
        self.wanted_name        : str                       = wanted_name
        self._response_handler                              = response_handler
        self._response_handler_tasks: set[asyncio.Future]   = set()

        self.aiobrowser         : Optional[AsyncServiceBrowser] = None
        self.aiozc              : Optional[AsyncZeroconf]       = None

    async def run(self):
        # get if on configured network that we'll open the socket on
        if_ips, _ = ifs.get_ifaces(self.network)
        if not if_ips:
            raise RuntimeError(f'No interfaces found that are connected to the configured network {self.network}')
        self._if_ip = if_ips[0]

        self.aiozc = AsyncZeroconf(interfaces=[self._if_ip])
        await self.aiozc.zeroconf.async_wait_for_start()
        self.aiobrowser = AsyncServiceBrowser(self.aiozc.zeroconf, [self.service], handlers=[self._on_service_state_change])
        try:
            # wait forever
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return  # cancellation processed
        finally:
            # cleanup
            await self.aiobrowser.async_cancel()
            await self.aiozc.async_close()

    def _on_service_state_change(self, zeroconf: Zeroconf, service_type: str, name: str, state_change: ServiceStateChange):
        if state_change is not ServiceStateChange.Added:
            return
        # check wanted service name (if looking for master of type _labManager._tcp.local. but get some other in the _labManager space, ignore)
        if name.removesuffix(service_type) != self.wanted_name:
            return
        async_thread.run(self._process_new_service(zeroconf, service_type, name))

    async def _process_new_service(self, zeroconf: Zeroconf, service_type: str, name: str):
        info = AsyncServiceInfo(service_type, name)
        await info.async_request(zeroconf, 3000)
        if not info or not self._response_handler:
            return
        addresses = [(addr, cast(int, info.port)) for addr in info.parsed_scoped_addresses()]
        if not addresses:
            return
        res = self._response_handler(addresses[0][0], addresses[0][1], info)
        # if awaitable, make sure its scheduled
        if inspect.isawaitable(res):
            task = asyncio.create_task(res)
            self._response_handler_tasks.add(task)
            task.add_done_callback(self._response_handler_tasks.discard)