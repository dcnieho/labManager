import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import List


SSDP_DEVICE_TYPE = "urn:schemas-upnp-org:device:labManager"

class CounterContext:
    _count = -1     # so that first number is 0

    def __enter__(self):
        self._count += 1

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    async def __aenter__(self):
        self.__enter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.__exit__(exc_type, exc_val, exc_tb)

    def get_count(self):
        return self._count

    def set_count(self, count):
        self._count = count

class AutoNameSpace(Enum):
    def _generate_next_value_(name, start, count, last_values):
        return name.strip("_").replace("__", "-").replace("_", " ")

class AutoNameDash(Enum):
    def _generate_next_value_(name, start, count, last_values):
        return name.lower().strip("_").replace("_", "-")


_client_id_provider = CounterContext()
@dataclass
class Client:
    writer  : asyncio.streams.StreamWriter
    
    id      : int = None
    host    : str = None
    port    : int = None
    name    : str = None

    tasks   : List['labManager.utils.task.Task'] = field(default_factory=lambda: [])

    def __post_init__(self):
        global _client_id_provider
        with _client_id_provider:
            self.id = _client_id_provider.get_count()

        self.host,self.port = self.writer.get_extra_info('peername')

    def __repr__(self):
        return f'{self.name}@{self.host}:{self.port}'
