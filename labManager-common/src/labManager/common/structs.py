from __future__ import annotations

import asyncio
import pathlib
from dataclasses import dataclass, field
from enum import auto

from . import counter, enum_helper, task


@enum_helper.get
class WaiterType(enum_helper.AutoNameDash):
    Client_Connect  = auto()    # wait for any (parameter is None), a specific number of clients (parameter is an int) or a specific client (parameter is a string) to connect
    Task            = auto()    # run for a specific task to complete
    Task_Group      = auto()    # wait for all tasks in a task group to complete
    File_Listing    = auto()    # wait for a file listing for a specific path to become available
    File_Action     = auto()    # wait for a specific file action to complete

@dataclass(frozen=True)
class Waiter:
    waiter_type : WaiterType
    parameter   : str|pathlib.Path|int
    fut         : asyncio.Future


# generic status for task or file action
@enum_helper.get
class Status(enum_helper.AutoNameSpace):
    Pending     = auto()
    Running     = auto()
    Finished    = auto()
    Errored     = auto()
statuses = [x.value for x in Status]


@dataclass
class ConnectedClient:
    reader          : asyncio.streams.StreamReader
    writer          : asyncio.streams.StreamWriter

    host            : str                   = None
    port            : int                   = None
    image_info      : dict[str,str]         = None
    eye_tracker     : eye_tracker           = None

    tasks           : dict[int, task.Task]  = field(default_factory=dict)
    et_events       : list[dict]            = field(default_factory=list)
    file_listings   : dict[str,dict]        = field(default_factory=dict)
    file_actions    : dict[int,dict]        = field(default_factory=dict)
    mounted_shares  : dict[str,str]         = field(default_factory=dict)

    _waiters        : set[Waiter]           = field(default_factory=set)

    def __post_init__(self):
        self.host,self.port = self.writer.get_extra_info('peername')

    def __repr__(self):
        return f'{self.name}@{self.host}:{self.port}'

_client_id_provider = counter.CounterContext()
@dataclass
class Client:
    name        : str
    MACs        : list[str]
    id          : int               = None
    known       : bool              = False

    online      : ConnectedClient   = None

    def __post_init__(self):
        global _client_id_provider
        with _client_id_provider:
            self.id = _client_id_provider.count

    def __repr__(self):
        return f'{self.name}@{self.MACs}, {"" if self.online else "not "}connected'


@dataclass
class DirEntry:
    name: str
    is_dir: bool
    full_path: pathlib.Path
    ctime: float
    mtime: float
    size: int
    mime_type: str