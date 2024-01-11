from __future__ import annotations

import asyncio
import pathlib
import datetime
from dataclasses import dataclass, field
from enum import auto
from functools import total_ordering

from . import counter, enum_helper, task


@enum_helper.get
class WaiterType(enum_helper.AutoNameDash):
    Login_Project_Select    = auto()    # wait for login and project selection to be finished
    Server_Started          = auto()    # wait for server to be started (use if you are not the one doing the starting, useless (but not dangerous) if you call start_server() yourself)
    Client_Connect_Any      = auto()    # wait for any client to connect (no parameters)
    Client_Connect_Name     = auto()    # wait for a specific client, by name, to connect (parameter is a string)
    Client_Disconnect_Any   = auto()    # wait for any client to disconnect (no parameters)
    Client_Disconnect_Name  = auto()    # wait for a specific client, by name, to disconnect (parameter is a string)
    Client_Connected_Nr     = auto()    # wait for a specific number of clients to be connected (parameter is an int) - Warning, does not fire when there are more or less than this number
    Task_Any                = auto()    # wait for any task to complete (no parameters)
    Task                    = auto()    # wait for a specific task to complete, by ID (parameter is int)
    Task_Group              = auto()    # wait for all tasks in a task group to complete, by ID (parameter is int)
    File_Listing            = auto()    # wait for a file listing for a specific path from a specific client to become available (parameter one is a string/path, parameter two is a client ID)
    File_Action             = auto()    # wait for a specific file action to complete (parameter is int, file action id)

@dataclass(frozen=True)
class Waiter:
    waiter_type : WaiterType
    parameter   : str|pathlib.Path|int|None
    parameter2  : int|None
    fut         : asyncio.Future


# generic status for task or file action
@enum_helper.get
@total_ordering # so file actions can be sorted by status in the GUI
class Status(enum_helper.AutoNameSpace):
    Pending     = auto()
    Running     = auto()
    Finished    = auto()
    Errored     = auto()
    def __lt__(self, other):
        if self.__class__ is other.__class__:
            order = [Status.Pending, Status.Running, Status.Finished, Status.Errored]
            return order.index(self) < order.index(other)
        return NotImplemented
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
        return f'ConnectedClient {self.host}:{self.port}'

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
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.ctime is not None and not isinstance(self.ctime, datetime.datetime):
            self.ctime = datetime.datetime.fromtimestamp(self.ctime)
        if self.mtime is not None and not isinstance(self.mtime, datetime.datetime):
            self.mtime = datetime.datetime.fromtimestamp(self.mtime)