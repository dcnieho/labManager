import socket
import os
import asyncio
import labManager.common.network.comms as comms

from ctypes import windll
from enum import IntEnum

SERVER_PORT = 555

# a bunch of the code here is taken from or adapted from https://github.com/dcnieho/labManager

def set_keepalive(sock, after_idle_sec=1, interval_sec=3):
    """Set TCP keepalive on an open socket.

    It activates after after_idle_sec seconds of idleness, then
    sends a keepalive ping once every interval_sec seconds.
    On Windowds Vista and later, the connection is closed after
    10 failed ping attempts, see:
    https://learn.microsoft.com/en-us/windows/win32/winsock/sio-keepalive-vals
    """
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    if hasattr(socket,'TCP_KEEPIDLE'):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, after_idle_sec)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, interval_sec)
    else:
        sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, int(after_idle_sec*1000), int(interval_sec*1000)))

def set_nagle(sock, on):
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, not on)


# https://win-precise-time.readthedocs.io/en/stable/examples.html#soft-realtime-example
class ProcessPriority(IntEnum):
    ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000
    BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
    HIGH_PRIORITY_CLASS = 0x00000080
    IDLE_PRIORITY_CLASS = 0x00000040
    NORMAL_PRIORITY_CLASS = 0x00000020
    PROCESS_MODE_BACKGROUND_BEGIN = 0x00100000
    PROCESS_MODE_BACKGROUND_END = 0x00200000
    REALTIME_PRIORITY_CLASS = 0x00000100

def set_process_priority(priority: ProcessPriority) -> None:
    pid = os.getpid()
    handle = windll.kernel32.OpenProcess(0x0200 | 0x0400, False, pid)
    windll.kernel32.SetPriorityClass(handle, priority)