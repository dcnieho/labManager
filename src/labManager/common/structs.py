import asyncio
from dataclasses import dataclass, field
from typing import Dict, List


class CounterContext:
    count = -1      # so that first number is 0

    def __enter__(self):
        self.count += 1
    async def __aenter__(self):
        self.__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.__exit__(exc_type, exc_val, exc_tb)


_client_id_provider = CounterContext()
@dataclass
class Client:
    writer  : asyncio.streams.StreamWriter

    id      : int = None
    host    : str = None
    port    : int = None
    MACs    : List[str] = None
    name    : str = None
    eye_tracker : 'labManager.common.eye_tracker.EyeTracker' = None

    known_client: 'KnownClient' = None

    tasks   : Dict[int, 'labManager.common.task.Task'] = field(default_factory=lambda: {})

    def __post_init__(self):
        global _client_id_provider
        with _client_id_provider:
            self.id = _client_id_provider.count

        self.host,self.port = self.writer.get_extra_info('peername')

    def __repr__(self):
        return f'{self.name}@{self.host}:{self.port}'


_known_client_id_provider = CounterContext()
@dataclass
class KnownClient:
    name        : str
    MAC         : str
    id          : int = None

    client      : Client = None

    def __post_init__(self):
        global _known_client_id_provider
        with _known_client_id_provider:
            self.id = _known_client_id_provider.count

    def __repr__(self):
        return f'{self.name}@{self.MAC}, {"" if self.client else "not "}connected'
