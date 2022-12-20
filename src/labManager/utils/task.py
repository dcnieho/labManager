import asyncio
import shlex
from enum import auto
from dataclasses import dataclass
from typing import Dict

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

_task_id_provider: structs.CounterContext = structs.CounterContext()
@dataclass
class Task:
    type        : Type
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
            
_task_group_id_provider: structs.CounterContext = structs.CounterContext()
@dataclass
class TaskGroup:
    task_refs   : Dict[int, Task]   # references to tasks belonging to this group, indexed by client name
    type        : Type
    payload     : str               # command, batch file contents, python script contents
    id          : int = None

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


async def _read_stream(stream, stream_type: StreamType, writer):  
    while True:
        line = await stream.readline()
        if line:
            await network.comms.typed_send(
                writer,
                network.message.Message.TASK_OUTPUT,
                {'stream_type':stream_type, 'output': line.decode('utf8')}
            )
        else:
            break

async def _stream_subprocess(cmd, writer):  
    cmd_list = shlex.split(cmd, posix=False)
    proc = await asyncio.create_subprocess_exec(
        *cmd_list,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await network.comms.typed_send(
        writer,
        network.message.Message.TASK_UPDATE,
        {'status': Status.Running}
    )

    await asyncio.wait([
        asyncio.create_task(_read_stream(proc.stdout, StreamType.STDOUT, writer)),
        asyncio.create_task(_read_stream(proc.stderr, StreamType.STDERR, writer))
    ])

    return_code = await proc.wait()
    await network.comms.typed_send(
        writer,
        network.message.Message.TASK_UPDATE,
        {'status': Status.Finished, 'return_code': return_code}
    )

    return return_code


async def execute(cmd, writer):
    rc = async_thread.run(
        _stream_subprocess(
            cmd,
            writer
        )
    )
    return rc

if __name__ == '__main__':  
    async_thread.setup()
    print(execute(
        r"C:\Users\huml-dkn\Downloads\ffmpeg-5.1.2-full_build-shared\bin\ffprobe.exe -i C:\dat\projects\sean_subpixel_cr\eye_videos\lossless\irisAccuracy2022_take2_ss01\cam1_R001.mp4",
        lambda x: print("STDOUT: %s" % x),
        lambda x: print("STDERR: %s" % x),
    ))
