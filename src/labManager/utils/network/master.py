import asyncio
import traceback
from typing import Dict, List, Tuple

from .. import async_thread, structs, task
from .  import comms, keepalive, message

class Server:
    def __init__(self):
        self.clients: Dict[int, structs.Client] = {}
        self.address = None

        self.task_groups: Dict[int, task.TaskGroup] = {}
        
    def add_client(self, client: structs.Client):
        self.clients[client.id] = client

    def remove_client(self, client: structs.Client):
        del self.clients[client.id]

    async def start(self, local_addr: Tuple[str,int]):
        self.server = await asyncio.start_server(self._handle_client, *local_addr)

        addr = [sock.getsockname() for sock in self.server.sockets]
        if len(addr[0])!=2:
            addr[0], addr[1] = addr[1], addr[0]
        self.address = addr

        # should already have started serving in asyncio.start_server, but to be save and sure:
        await self.server.start_serving()

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()

    async def _handle_client(self, reader: asyncio.streams.StreamReader, writer: asyncio.streams.StreamWriter):
        keepalive.set(writer.get_extra_info('socket'))

        me = structs.Client(writer)
        self.add_client(me)

        # request info about client
        await comms.typed_send(writer, message.Message.IDENTIFY)
    
        # process incoming messages
        type = None
        while type != message.Message.QUIT:
            try:
                type, msg = await comms.typed_receive(reader)
                if not type:
                    # connection broken, close
                    break

                match type:
                    case message.Message.IDENTIFY:
                        me.name = msg
                        print(f'setting name for {me.host}:{me.port} to: {msg}')
                    case message.Message.INFO:
                        print(f'{me.host}:{me.port}: {msg}')
                    case message.Message.TASK_OUTPUT:
                        mytask = me.tasks[msg['task_id']]
                        match msg['stream_type']:
                            case task.StreamType.STDOUT:
                                mytask.stdout += msg['output']
                            case task.StreamType.STDERR:
                                mytask.stderr += msg['output']
                    case message.Message.TASK_UPDATE:
                        mytask = me.tasks[msg['task_id']]
                        mytask.status = msg['status']
                        if 'return_code' in msg:
                            mytask.return_code = msg['return_code']
                    case _:
                        print(f'got unhandled type {type.value}, message: {msg}')
 
            except Exception as exc:
                tb_lines = traceback.format_exception(exc)
                print("".join(tb_lines))
                continue

        writer.close()

        # remove from client list
        self.remove_client(me)

    async def broadcast(self, type: message.Message, message: str=''):
        for c in self.clients:
            await comms.typed_send(self.clients[c].writer, type, message)

    async def run_task(self, type: task.Type, payload: str, clients: List[int] | str):
        # clients has a special value '*' which means all clients
        if clients=='*':
            clients = [c for c in self.clients]

        # make task group
        task_group = task.create_group(type, payload, clients)
        self.task_groups[task_group.id] = task_group

        # start tasks
        coros = []
        for c in task_group.task_refs:
            mytask = task_group.task_refs[c]
            # add to client task list
            self.clients[c].tasks[mytask.id] = mytask
            # send
            coros.append(task.send(mytask, self.clients[c].writer))

        await asyncio.gather(*coros)