import asyncio
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Dict


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

class AutoName(Enum):
    def _generate_next_value_(name, start, count, last_values):
        return name.strip("_").replace("__", "-").replace("_", " ")
        
class TaskType(AutoName):
    Shell_command   = auto()   # shell subprocess
    Process_exec    = auto()   # executable subprocess
    Batch_file      = auto()
    Python_statement= auto()   # sys.executable + '-c'
    Python_script   = auto()
task_types = [x.value for x in TaskType]

class Status(AutoName):
    Not_started     = auto()
    Running         = auto()
    Finished        = auto()
    Errored         = auto()
status_names = [x.value for x in Status]

_task_id_provider: CounterContext = CounterContext()
@dataclass
class Task:
    type        : TaskType
    payload     : str           # command, batch file contents, python script contents
    id          : int = None
    status      : Status = Status.Not_started

    task_group_id: int = None

    # when running, client starts sending these back as they become available:
    stdout      : str = ''
    stderr      : str = ''
    # when status finished or errored, client provides the return code:
    return_code : int = None

    chain_id    : int = None    # if non-zero, start task with this id after the current task has finished (not errored)

    def __post_init__(self):
        global _task_id_provider
        with _task_id_provider:
            self.id = _task_id_provider.get_count()
            
_task_group_id_provider: CounterContext = CounterContext()
@dataclass
class TaskGroup:
    task_refs   : Dict[int, Task]   # references to tasks belonging to this group, indexed by client name
    type        : TaskType
    payload     : str               # command, batch file contents, python script contents
    id          : int = None

    num_finished: int = 0
    status      : Status = Status.Not_started   # running when any task has started, error when any has errored, finished when all finished successfully

    chain_id    : int = None        # if non-zero, indicates task group of tasks that will start after tasks in this group have finished (not errored)

    def __post_init__(self):
        global _task_group_id_provider
        with _task_group_id_provider:
            self.id = _task_group_id_provider.get_count()

@dataclass
class Client:
    writer  : asyncio.streams.StreamWriter
    
    host    : str = None
    port    : int = None
    name    : str = None

    tasks   : List[Task] = field(default_factory=lambda: [])

    def __post_init__(self):
        self.host,self.port = self.writer.get_extra_info('peername')

    def __repr__(self):
        return f'{self.name}@{self.host}:{self.port}'
