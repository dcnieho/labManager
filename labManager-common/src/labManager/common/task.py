import asyncio
import aiofile
import shlex
import shutil
import pathlib
import traceback
import sys
from enum import auto
from dataclasses import dataclass, field
from typing import Dict, List

from . import enum_helper, message, structs
from .network import comms, wol

# TODO: env is a dict and should support either adding or overriding specific variables
# https://stackoverflow.com/questions/2231227/python-subprocess-popen-with-a-modified-environment

@enum_helper.get('task types')
class Type(enum_helper.AutoNameSpace):
    Shell_command   = auto()    # run command in shell
    Process_exec    = auto()    # run executable
    Batch_file      = auto()    # invoke batch file
    Python_statement= auto()    # sys.executable + '-c'
    Python_module   = auto()    # sys.executable + '-m'
    Python_script   = auto()    # sys.executable, invoke python script
    Wake_on_LAN     = auto()    # special task to broadcast WoL packets (TODO)
types = [x.value for x in Type]

Type.Shell_command   .doc = 'Run command in shell'
Type.Process_exec    .doc = 'Run executable'
Type.Batch_file      .doc = 'Invoke batch file'
Type.Python_statement.doc = 'Call client''s active python.exe (sys.executable) with -c command line switch'
Type.Python_module   .doc = 'Call client''s active python.exe (sys.executable) with -m command line switch'
Type.Python_script   .doc = 'Execute Python script with the client''s active python.exe (sys.executable)'
Type.Wake_on_LAN     .doc = 'Send Wake on LAN command'

@enum_helper.get('task statuses')
class Status(enum_helper.AutoNameSpace):
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
    cwd         : str = None    # if not None, working directory to execute from
    env         : dict= None    # if not None, environment variables when executing
    id          : int = None
    status      : Status = Status.Not_started
    interactive : bool = False  # if True, stdin is connected to a pipe and commands can be sent by master to control

    known_client: int = None
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
            self.id = _task_id_provider.count

@dataclass
class RunningTask:
    id                  : int
    async_task          : asyncio.Task = None
    input               : asyncio.Queue= None
    tried_stdin_close   : bool = False


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
            self.id = _task_group_id_provider.count

@enum_helper.get('stream types')
class StreamType(enum_helper.AutoNameDash):
    STDOUT      = auto()
    STDERR      = auto()



# create instances through Executor.run()
class Executor:
    def __init__(self):
        self._proc: asyncio.subprocess.Process = None
        self._input: asyncio.Queue = None

    async def _read_stream(self, stream, stream_type: StreamType, writer, id):
        while True:
            line = await stream.read(20)
            if line:
                await comms.typed_send(
                    writer,
                    message.Message.TASK_OUTPUT,
                    {'task_id': id, 'stream_type': stream_type, 'output': line.decode('utf8')}
                )
            else:
                break

    async def _write_stream(self, stream):
        while True:
            input = await self._input.get()
            if input is not None:
                stream.write(input.encode())
                await stream.drain()
            else:
                break
        stream.close()

    async def _stream_subprocess(self, id, use_shell, cmd, cwd, env, interactive, writer, cleanup=None):
        try:
            if use_shell:
                self._proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdin=asyncio.subprocess.PIPE if interactive else None,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=None if not cwd else cwd,
                    env=None if not env else env,
                )
            else:
                self._proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE if interactive else None,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=None if not cwd else cwd,
                    env=None if not env else env,
                )
        except Exception as exc:
            await self._handle_error(exc, id, writer)
            # we're done
            return None

        # send that we're running
        await comms.typed_send(
            writer,
            message.Message.TASK_UPDATE,
            {'task_id': id, 'status': Status.Running}
        )

        # listen to output streams and forward to master
        tasks = [
            asyncio.create_task(self._read_stream(self._proc.stdout, StreamType.STDOUT, writer, id)),
            asyncio.create_task(self._read_stream(self._proc.stderr, StreamType.STDERR, writer, id))
        ]
        if interactive:
            tasks.append(asyncio.create_task(self._write_stream(self._proc.stdin)))
        await asyncio.wait(tasks)

        # wait for return code to become available and forward to master
        return_code = await self._proc.wait()
        await comms.typed_send(
            writer,
            message.Message.TASK_UPDATE,
            {
                'task_id': id,
                'status': Status.Finished if return_code==0 else Status.Errored,
                'return_code': return_code
            }
        )

        # clean up if needed
        if cleanup:
            shutil.rmtree(cleanup.parent,ignore_errors=True)

        return return_code

    async def run(self, id: int, type: Type, payload: str, cwd: str, env: dict, interactive: bool, running_task: RunningTask, writer):
        # setup executor
        match type:
            case Type.Shell_command:
                use_shell = True
            case _:
                use_shell = False

        # build command line
        filename = None
        match type:
            case Type.Shell_command:
                # run command in shell
                cmd = payload
            case Type.Process_exec:
                # run executable
                cmd = shlex.split(payload, posix=False)
            case Type.Batch_file:
                # invoke batch file
                folder   = pathlib.Path(f'task{id}')
                filename = (folder/'script.bat').resolve()
                cmd = [str(filename)]
            case Type.Python_statement:
                # sys.executable + '-c'
                cmd = [sys.executable, '-c'] + shlex.split(payload, posix=False)
            case Type.Python_module:
                # sys.executable + '-m'
                cmd = [sys.executable, '-m'] + shlex.split(payload, posix=False)
            case Type.Python_script:
                # sys.executable
                folder   = pathlib.Path(f'task{id}')
                filename = (folder/'script.py').resolve()
                cmd = [sys.executable, str(filename)]

        # write payload to file if needed
        if filename:
            if folder.is_dir():
                shutil.rmtree(folder,ignore_errors=True)
            folder.mkdir()
            async with aiofile.async_open(filename, 'wt') as afp:
                await afp.write(payload)

        # prep for input stream, if needed
        if interactive:
            self._input = running_task.input = asyncio.Queue()

        # create coro to execute the command, await it to execute it
        try:
            return await self._stream_subprocess(
                id,
                use_shell,
                cmd,
                cwd,
                env,
                interactive,
                writer,
                cleanup=filename
            )
        except asyncio.CancelledError as exc:
            # notify master about cancellation and terminate task if necessary
            await self._handle_error(exc, id, writer)
            if self._proc:
                self._proc.terminate()
                await self._proc.wait()
            raise   # as far as i understand the docs, this Exception should be propagated

    async def _handle_error(self, exc, id, writer):
            tb_lines = traceback.format_exception(exc)
            # send error text
            await comms.typed_send(
                writer,
                message.Message.TASK_OUTPUT,
                {'task_id': id, 'stream_type': StreamType.STDERR, 'output': "".join(tb_lines)}
            )
            # send error status
            await comms.typed_send(
                writer,
                message.Message.TASK_UPDATE,
                {'task_id': id, 'status': Status.Errored}
            )

async def send(task: Task|TaskGroup, known_client):
    if isinstance(task, TaskGroup):
        if task.type==Type.Wake_on_LAN:
            MACs = [known_client[task.task_refs[i].known_client].MAC for i in task.task_refs if task.task_refs[i].known_client in known_client]
            await wol.send_magic_packet(*MACs)
            for _,t in task.task_refs.items():
                t.status = Status.Finished  # This task is finished once its sent
        else:
            raise RuntimeError(f'API usage error: Task type {task.Type.value} cannot be launched as a group at once. Do this only if the second return argument of task.create_group() is True')
    else:
        if task.type==Type.Wake_on_LAN:
            await wol.send_magic_packet(known_client.MAC)
            task.status = Status.Finished   # This task is finished once its sent
        elif known_client.client:
            await comms.typed_send(
                known_client.client.writer,
                message.Message.TASK_CREATE,
                {
                    'task_id': task.id,
                    'type': task.type,
                    'payload': task.payload,
                    'cwd': task.cwd,
                    'env': task.env,
                    'interactive': task.interactive,
                }
            )

async def send_input(payload, known_client, task: Task):
    if known_client.client:
        await comms.typed_send(
            known_client.client.writer,
            message.Message.TASK_INPUT,
            {
                'task_id': task.id,
                'payload': payload,
            }
        )

async def send_cancel(known_client, task: Task):
    if known_client.client:
        await comms.typed_send(
            known_client.client.writer,
            message.Message.TASK_CANCEL,
            {
                'task_id': task.id,
            }
        )

def create_group(type: Type, payload: str, known_clients: List[int], cwd: str=None, env: dict=None, interactive=False) -> TaskGroup:
    task_group = TaskGroup(type, payload)

    # make individual tasks
    for c in known_clients:
        # create task
        task = Task(type, payload, cwd=cwd, env=env, interactive=interactive, known_client=c, task_group_id=task_group.id)
        # add to task group
        task_group.task_refs[c] = task

    # second return: true if whole group should be launched as one, false if tasks should be launched individually
    return task_group, type==type.Wake_on_LAN