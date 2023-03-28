# based on https://github.com/remcohaszing/pywakeonlan 3.0.0, but using asyncio
# see https://stackoverflow.com/a/75865998/3103767 for details on the implementation
import asyncio
import socket
from typing import Optional

BROADCAST_IP = "255.255.255.255"
DEFAULT_PORT = 9

def create_magic_packet(macaddress: str) -> bytes:
    """
    Create a magic packet.
    A magic packet is a packet that can be used with the for wake on lan
    protocol to wake up a computer. The packet is constructed from the
    mac address given as a parameter.
    Args:
        macaddress: the mac address that should be parsed into a magic packet.
    """
    if len(macaddress) == 17:
        sep = macaddress[2]
        macaddress = macaddress.replace(sep, "")
    elif len(macaddress) == 14:
        sep = macaddress[4]
        macaddress = macaddress.replace(sep, "")
    if len(macaddress) != 12:
        raise ValueError("Incorrect MAC address format")
    return bytes.fromhex("F" * 12 + macaddress * 16)


class _WOLProtocol(asyncio.DatagramProtocol):
    def __init__(self, remote_addr, *macs):
        self.remote_addr = remote_addr
        self.packets = [create_magic_packet(mac) for mac in macs]
        self.done = asyncio.get_running_loop().create_future()
        self._waiter = None
        self.transport = None

    async def wait_until_sent(self):
        while True:
            await asyncio.sleep(0)
            if self.transport.get_write_buffer_size()==0:
                break

        if not self.done.done():
            self.done.set_result(None)

    def connection_made(self, transport):
        self.transport = transport
        for p in self.packets:
            self.transport.sendto(p, (BROADCAST_IP,DEFAULT_PORT))

        self._waiter = asyncio.create_task(self.wait_until_sent())

    def error_received(self, exc):
        if not self.done.done():
            self.done.set_exception(exc)

    def connection_lost(self, exc):
        if not self.done.done():
            self.done.set_result(None)


async def send_magic_packet(
    *macs: str,
    ip_address: str = BROADCAST_IP,
    port: int = DEFAULT_PORT,
    interface: Optional[str] = None
) -> None:
    """
    Wake up computers having any of the given mac addresses.
    Wake on lan must be enabled on the host device.
    Args:
        macs: One or more macaddresses of machines to wake.
    Keyword Args:
        ip_address: the ip address of the host to send the magic packet to.
        port: the port of the host to send the magic packet to.
        interface: the ip address of the network adapter to route the magic packet through.
    """

    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: _WOLProtocol((ip_address, port), *macs),
        family=socket.AF_INET,
        proto=socket.IPPROTO_UDP,
        allow_broadcast = True,
        local_addr=(interface, 0) if interface else None
        )

    try:
        await protocol.done
    finally:
        transport.close()