import asyncio
import shlex
import traceback
import sys
from enum import auto
from dataclasses import dataclass, field
from typing import Dict, List

from . import async_thread, enum_helper, network, structs

@enum_helper.get('task types')
class Type(structs.AutoNameSpace):
    Shell_command   = auto()    # run command in shell
    Process_exec    = auto()    # run executable
    Batch_file      = auto()    # invoke batch file
    Python_statement= auto()    # sys.executable + '-c'
    Python_module   = auto()    # sys.executable + '-m'
    Python_script   = auto()    # sys.executable
types = [x.value for x in Type]

@enum_helper.get('task statuses')
class Status(structs.AutoNameSpace):
    Not_started     = auto()
    Running         = auto()
    Finished        = auto()
    Errored         = auto()
statuses = [x.value for x in Status]

_task_id_provider = structs.CounterContext()
@dataclass
class Task:
    type        : Type
    payload     : str           # command, batch file contents, python script contents
    id          : int = None
    status      : Status = Status.Not_started

    client      : int = None
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
            
_task_group_id_provider = structs.CounterContext()
@dataclass
class TaskGroup:
    type        : Type
    payload     : str               # command, batch file contents, python script contents
    id          : int = None
    
    # references to tasks belonging to this group, indexed by client name
    task_refs   : Dict[int, Task]  = field(default_factory=lambda: {})
    
    num_finished: int = 0
    status      : Status = Status.Not_started   # running when any task has started, error when any has errored, finished when all finished successfully

    chain_id    : int = None        # if non-zero, indicates task group of tasks that will start after tasks in this group have finished (not errored)

    def __post_init__(self):
        global _task_group_id_provider
        with _task_group_id_provider:
            self.id = _task_group_id_provider.get_count()

@enum_helper.get('stream types')
class StreamType(structs.AutoNameDash):
    STDOUT      = auto()
    STDERR      = auto()



async def _read_stream(stream, stream_type: StreamType, writer, id):  
    while True:
        line = await stream.read(20)
        if line:
            await network.comms.typed_send(
                writer,
                network.message.Message.TASK_OUTPUT,
                {'task_id': id, 'stream_type': stream_type, 'output': line.decode('utf8')}
            )
        else:
            break

async def _stream_subprocess(id, use_shell, cmd, writer):
    try:
        if use_shell:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
    except Exception as exc:
        tb_lines = traceback.format_exception(exc)
        # send error text
        await network.comms.typed_send(
            writer,
            network.message.Message.TASK_OUTPUT,
            {'task_id': id, 'stream_type': StreamType.STDERR, 'output': "".join(tb_lines)}
        )
        # send error status
        await network.comms.typed_send(
            writer,
            network.message.Message.TASK_UPDATE,
            {'task_id': id, 'status': Status.Errored}
        )
        # we're done
        return None
    
    # send that we're running
    await network.comms.typed_send(
        writer,
        network.message.Message.TASK_UPDATE,
        {'task_id': id, 'status': Status.Running}
    )

    # listen to output streams and forward to master
    await asyncio.wait([
        asyncio.create_task(_read_stream(proc.stdout, StreamType.STDOUT, writer, id)),
        asyncio.create_task(_read_stream(proc.stderr, StreamType.STDERR, writer, id))
    ])

    # wait for return code to become available and forward to master
    return_code = await proc.wait()
    await network.comms.typed_send(
        writer,
        network.message.Message.TASK_UPDATE,
        {
            'task_id': id,
            'status': Status.Finished if return_code==0 else Status.Errored,
            'return_code': return_code
        }
    )

    return return_code


async def execute(id: int, type: Type, cmd: str, writer):
    # setup executor
    match type:
        case Type.Shell_command:
            use_shell = True
        case _:
            use_shell = False

    # build command line
    match type:
        case Type.Shell_command:
            # run command in shell
            pass    # nothing to do
        case Type.Process_exec:
            # run executable
            cmd = shlex.split(cmd, posix=False)
        case Type.Batch_file:
            # invoke batch file
            pass    # TODO
        case Type.Python_statement:
            # sys.executable + '-c'
            cmd = [sys.executable, '-c'] + shlex.split(cmd, posix=False)
        case Type.Python_module:
            # sys.executable + '-m'
            cmd = [sys.executable, '-m'] + shlex.split(cmd, posix=False)
        case Type.Python_script:
            # sys.executable
            pass    # TODO
        
    # create coro to execute the command, await it to execute it
    return await _stream_subprocess(
        id,
        use_shell,
        cmd,
        writer
    )

async def send(task: Task, writer):
    await network.comms.typed_send(
        writer,
        network.message.Message.TASK_CREATE,
        {
            'task_id': task.id,
            'type': task.type,
            'payload': task.payload
        }
    )

def create_group(type: Type, payload: str, clients: List[int]) -> TaskGroup:
    task_group = TaskGroup(type, payload)

    # make individual tasks
    for c in clients:
        # create task
        task = Task(type, payload, client=c, task_group_id=task_group.id)
        # add to task group
        task_group.task_refs[c] = task

    return task_group