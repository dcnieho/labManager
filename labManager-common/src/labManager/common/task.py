from __future__ import annotations

import asyncio
import aiopath
import aioshutil
import copy
import shlex
import shutil
import pathlib
import traceback
import sys
from enum import auto
from dataclasses import dataclass, field
from typing import Callable

from . import counter, enum_helper, message, structs
from .network import comms, wol

# TODO: env is a dict and should support either adding or overriding specific variables
# https://stackoverflow.com/questions/2231227/python-subprocess-popen-with-a-modified-environment

@enum_helper.get
class Type(enum_helper.AutoNameSpace):
    Shell_command   = auto()    # run command in shell
    Process_exec    = auto()    # run executable
    Batch_file      = auto()    # invoke batch file
    Python_module   = auto()    # sys.executable + '-m'
    Python_script   = auto()    # sys.executable, invoke python script
    Wake_on_LAN     = auto()    # special task to broadcast WoL packets
types = [x.value for x in Type]

Type.Shell_command   .doc = 'Run command in shell'
Type.Process_exec    .doc = 'Run executable'
Type.Batch_file      .doc = 'Invoke batch file'
Type.Python_module   .doc = 'Call client''s active python.exe (sys.executable) with -m command line switch'
Type.Python_script   .doc = 'Execute Python script with the client''s active python.exe (sys.executable)'
Type.Wake_on_LAN     .doc = 'Send Wake on LAN command'

_task_id_provider = counter.CounterContext()
@dataclass
class Task:
    type        : Type
    payload     : str           # command, batch file contents, python script contents
    cwd         : str = None    # if not None, working directory to execute from
    env         : dict= None    # if not None, environment variables when executing
    interactive : bool = False  # if True, stdin is connected to a pipe and commands can be sent by master to control
    python_unbuf: bool= False   # if task.Type is Python_module or Python_script, specify whether the -u flag should be passed to run in unbuffered mode

    id          : int = None
    status      : structs.Status    # after https://stackoverflow.com/a/61480946/3103767
    _status     : structs.Status = field(init=False, repr=False, default=structs.Status.Pending)

    client      : int = None
    task_group_id: int = None

    # when running, client starts sending back stdout and stderr as they become available. Buffer to store them in:
    output      : str = ''
    # when status finished or errored, client provides the return code:
    return_code : int = None

    _listeners: list[Callable[[Task], None]] = field(default_factory=list)

    def __post_init__(self):
        global _task_id_provider
        with _task_id_provider:
            self.id = _task_id_provider.count

    @property
    def status(self) -> structs.Status:
        return self._status

    @status.setter
    def status(self, value: structs.Status) -> None:
        if isinstance(value, property):
            # initial value not specified, use default
            self._status = Task._status
            return

        self._status = value

        # call any value changed observers
        to_del = []
        for i,c in enumerate(self._listeners):
            try:
                c(self)
            except:
                to_del.append(i)
        # remove crashing hooks so they are not called again
        for i in to_del[::-1]:
            del self._listeners[i]

    def add_listener(self, callback: Callable[[Task], None]):
        self._listeners.append(callback)

    def is_done(self):
        return self.status in [structs.Status.Finished, structs.Status.Errored]

@dataclass
class TaskDef:
    name        : str       = ''    # just for showing in GUI
    type        : Type      = Type.Shell_command   # good default
    payload_type: str       = 'text'
    payload_text: str       = ''
    payload_file: str       = ''
    cwd         : str       = ''
    env         : dict      = field(default_factory=dict)
    interactive : bool      = False
    python_unbuf: bool      = False

    @classmethod
    def fromtask(cls, task: Task):
        "Initialize TaskDef from a Task"
        return cls(type=task.type, payload_text=task.payload, cwd=task.cwd, env=copy.deepcopy(task.env), interactive=task.interactive, python_unbuf=task.python_unbuf)

    @classmethod
    def fromdict(cls, task: dict):
        tdef = cls(type=Type(task['type']))
        if 'name' in task:
            tdef.name = task['name']
        tdef.payload_type= task['payload_type']
        if task['payload_type']=='text':
            tdef.payload_text = task['payload']
        else:
            tdef.payload_file = task['payload']
        tdef.cwd         = task['cwd']
        tdef.env         = task['env']
        tdef.interactive = task['interactive']
        tdef.python_unbuf= task['python_unbuffered']
        return tdef

@dataclass
class RunningTask:
    id                  : int
    handler             : asyncio.Task = None
    input               : asyncio.Queue= None
    tried_stdin_close   : bool = False


_task_group_id_provider = counter.CounterContext()
@dataclass
class TaskGroup:
    type        : Type
    id          : int = None

    # references to tasks belonging to this group
    # index is client id
    tasks       : dict[int, Task]  = field(default_factory=dict)

    num_finished: int = 0
    # status: running when any task has started, error when any has errored, finished when all finished successfully
    status      : structs.Status    # after https://stackoverflow.com/a/61480946/3103767
    _status     : structs.Status = field(init=False, repr=False, default=structs.Status.Pending)

    _listeners: list[Callable[[TaskGroup], None]] = field(default_factory=list)

    def __post_init__(self):
        global _task_group_id_provider
        with _task_group_id_provider:
            self.id = _task_group_id_provider.count

    def add_task(self, client_id: int, tsk: Task):
        self.tasks[client_id] = tsk
        tsk.add_listener(self._on_task_state_change)

    def _on_task_state_change(self, tsk: Task):
        if tsk.id not in [self.tasks[c].id for c in self.tasks]:
            # task not a part of this task group (shouldn't occur?), nothing to do
            return
        if tsk.status==structs.Status.Running and self.status!=structs.Status.Running:
            self.status = structs.Status.Running

        # rest of logic is for when the task is finished
        if not tsk.is_done():
            return
        self.num_finished += 1
        if tsk.status==structs.Status.Errored:
            # task group status is errored when any task has errored
            self.status = structs.Status.Errored
        elif self.num_finished==len(self.tasks):
            # task group status is finished when all tasks have finished
            self.status = structs.Status.Finished

    @property
    def status(self) -> structs.Status:
        return self._status

    @status.setter
    def status(self, value: structs.Status) -> None:
        if isinstance(value, property):
            # initial value not specified, use default
            self._status = TaskGroup._status
            return

        self._status = value

        # call any value changed observers
        to_del = []
        for i,c in enumerate(self._listeners):
            try:
                c(self)
            except:
                to_del.append(i)
        # remove crashing hooks so they are not called again
        for i in to_del[::-1]:
            del self._listeners[i]

    def add_listener(self, callback: Callable[[TaskGroup], None]):
        self._listeners.append(callback)

    def is_done(self):
        return self.status in [structs.Status.Finished, structs.Status.Errored]


@enum_helper.get
class StreamType(enum_helper.AutoNameDash):
    STDOUT      = auto()
    STDERR      = auto()



# create instances through Executor.run()
class Executor:
    def __init__(self):
        self._proc: asyncio.subprocess.Process = None
        self._input: asyncio.Queue = None

    async def _read_stream(self, stream: asyncio.streams.StreamReader, stream_type: StreamType, writer, task_id):
        full_line = b''
        while True:
            line = await stream.read(20)
            if line:
                try:
                    msg = (full_line+line).decode('utf8')
                except UnicodeDecodeError as exc:
                    if len(full_line)+len(line)==exc.end:
                        # our read split a utf8 codepoint in two
                        # add to next read and try again
                        full_line += line
                        msg = None
                    else:
                        # replace string with error message
                        msg = '<UnicodeDecodeError>'
                finally:
                    if msg is None:
                        continue
                    full_line = b''
                    await comms.typed_send(
                        writer,
                        message.Message.TASK_OUTPUT,
                        {'task_id': task_id, 'stream_type': stream_type, 'output': msg}
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
            {'task_id': id, 'status': structs.Status.Running}
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
                'status': structs.Status.Finished if return_code==0 else structs.Status.Errored,
                'return_code': return_code
            }
        )

        # clean up if needed
        if cleanup:
            shutil.rmtree(cleanup.parent,ignore_errors=True)

        return return_code

    async def run(self, id: int, tsk_type: Type, payload: str, cwd: str, env: dict, interactive: bool, python_unbuf: bool, running_task: RunningTask, writer):
        # setup executor
        match tsk_type:
            case Type.Shell_command:
                use_shell = True
            case _:
                use_shell = False

        # build command line
        filename = None
        match tsk_type:
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
            case Type.Python_module:
                # sys.executable + '-m' + '-u' for unbuffered so that we get all output to stdout/stderr piped to us directly
                cmd = [sys.executable, '-m']
                if python_unbuf:
                    cmd += ['-u']
                cmd += shlex.split(payload, posix=False)
            case Type.Python_script:
                # sys.executable + '-u' for unbuffered so that we get all output to stdout/stderr piped to us directly
                folder   = pathlib.Path(f'task{id}')
                filename = (folder/'script.py').resolve()
                cmd = [sys.executable]
                if python_unbuf:
                    cmd += ['-u']
                cmd += [str(filename)]
            case _:
                raise ValueError(f'Task type {tsk_type} not understood')

        # write payload to file if needed
        if filename:
            folder = aiopath.AsyncPath(folder)
            if await folder.is_dir():
                await aioshutil.rmtree(folder,ignore_errors=True)
            await folder.mkdir()
            await aiopath.AsyncPath(filename).write_text(payload)

        # prep for input stream, if needed
        if interactive:
            self._input = running_task.input = asyncio.Queue()

        # TODO: deal with env argument. Should probably get current env and append to it/overwrite, not replace

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
                {'task_id': id, 'status': structs.Status.Errored}
            )

async def send(task: Task|TaskGroup, client: list[structs.Client]|structs.Client):
    if isinstance(task, TaskGroup):
        if task.type==Type.Wake_on_LAN:
            if task.tasks:
                MACs = [client[task.tasks[i].client].MACs for i in task.tasks if task.tasks[i].client in client]
                MACs = [m for mac in MACs for m in mac]
                await wol.send_magic_packet(*MACs)
                for _,t in task.tasks.items():
                    t.status = structs.Status.Finished  # This task is finished once its sent
        else:
            raise RuntimeError(f'API usage error: Task type {task.Type.value} cannot be launched as a group at once. Do this only if the second return argument of task.create_group() is True')
    else:
        if task.type==Type.Wake_on_LAN:
            await wol.send_magic_packet(*client.MACs)
            task.status = structs.Status.Finished   # This task is finished once its sent
        elif client.online:
            await comms.typed_send(
                client.online.writer,
                message.Message.TASK_CREATE,
                {
                    'task_id': task.id,
                    'type': task.type,
                    'payload': task.payload,
                    'cwd': task.cwd,
                    'env': task.env,
                    'interactive': task.interactive,
                    'python_unbuf': task.python_unbuf,
                }
            )

async def send_input(payload, client, task: Task):
    if client.online:
        await comms.typed_send(
            client.online.writer,
            message.Message.TASK_INPUT,
            {
                'task_id': task.id,
                'payload': payload,
            }
        )

async def send_cancel(client, task: Task):
    if client.online:
        await comms.typed_send(
            client.online.writer,
            message.Message.TASK_CANCEL,
            {
                'task_id': task.id,
            }
        )

def create_group(tsk_type: str|Type, payload: str, clients: list[int], cwd: str=None, env: dict=None, interactive=False, python_unbuf=False) -> tuple[TaskGroup, bool]:
    tsk_type = Type.get(tsk_type)
    task_group = TaskGroup(tsk_type)

    # make individual tasks
    for c in clients:
        # create task
        task = Task(tsk_type, payload, cwd=cwd, env=env, interactive=interactive, python_unbuf=python_unbuf, client=c, task_group_id=task_group.id)
        # add to task group
        task_group.add_task(c,task)

    return task_group

def task_group_launch_as_group(task_group: TaskGroup):
    # true if whole group should be launched as one, false if tasks should be launched individually
    return task_group.type==Type.Wake_on_LAN