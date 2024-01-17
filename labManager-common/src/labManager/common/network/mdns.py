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


async def _get_aiozc(network):
    # get if on configured network that we'll open the socket on
    if_ips, _ = ifs.get_ifaces(network)
    if not if_ips:
        raise RuntimeError(f'No interfaces found that are connected to the configured network {network}')
    if_ip = if_ips[0]

    aiozc = AsyncZeroconf(interfaces=[if_ip])
    await aiozc.zeroconf.async_wait_for_start()
    return if_ip, aiozc

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
        self._if_ip, self.aiozc = await _get_aiozc(self.network)

        # start announce
        await (await self.aiozc.async_register_service(self.info))

        # wait forever
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            return  # cancellation processed
        finally:
            # cleanup
            tasks = [self.aiozc.async_unregister_service(self.info)]
            background_tasks = await asyncio.gather(*tasks)
            await asyncio.gather(*background_tasks)
            await self.aiozc.async_close()


class Discoverer:
    def __init__(self, ip_network: str|ipaddress.IPv4Network, service: str, response_handler = None):
        network = ipaddress.IPv4Network(ip_network)
        if network.num_addresses > 512:
            raise ValueError(f'Too many addresses in network {ip_network}')
        self.network            : ipaddress.IPv4Network     = network
        self._if_ip             : str                       = None

        self.service            : str                       = service
        self._response_handler                              = response_handler
        self._response_handler_tasks: set[asyncio.Future]   = set()

        self.aiobrowser         : Optional[AsyncServiceBrowser] = None
        self.aiozc              : Optional[AsyncZeroconf]       = None

    async def run(self):
        self._if_ip, self.aiozc = await _get_aiozc(self.network)
        self.aiobrowser = AsyncServiceBrowser(self.aiozc.zeroconf, [self.service], handlers=[self._on_service_state_change])

        # wait forever
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            return  # cancellation processed
        finally:
            # cleanup
            await self.aiobrowser.async_cancel()
            await self.aiozc.async_close()

    def _on_service_state_change(self, zeroconf: Zeroconf, service_type: str, name: str, state_change: ServiceStateChange):
        # NB: updated may occur is master just disappeared without deregistering
        if state_change not in [ServiceStateChange.Added, ServiceStateChange.Updated]:
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