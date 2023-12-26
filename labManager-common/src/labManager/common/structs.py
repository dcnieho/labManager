from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from . import counter, task


@dataclass
class ConnectedClient:
    reader          : asyncio.streams.StreamReader
    writer          : asyncio.streams.StreamWriter

    host            : str                   = None
    port            : int                   = None
    image_info      : dict[str,str]         = None
    eye_tracker     : eye_tracker           = None

    tasks           : dict[int, task.Task]  = field(default_factory=lambda: {})
    et_events       : list[dict]            = field(default_factory=lambda: [])
    file_listings   : dict[str,dict]        = field(default_factory=lambda: {})
    mounted_shares  : dict[str,str]         = field(default_factory=lambda: {})

    def __post_init__(self):
        self.host,self.port = self.writer.get_extra_info('peername')

    def __repr__(self):
        return f'{self.name}@{self.host}:{self.port}'


_client_id_provider = counter.CounterContext()
@dataclass
class Client:
    name        : str
    MACs        : list[str]
    id          : int = None
    known       : bool = False

    online      : ConnectedClient = None

    def __post_init__(self):
        global _client_id_provider
        with _client_id_provider:
            self.id = _client_id_provider.count

    def __repr__(self):
        return f'{self.name}@{self.MACs}, {"" if self.online else "not "}connected'