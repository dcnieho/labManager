import asyncio
import concurrent
import traceback
from typing import Dict, Tuple

from .. import async_thread, structs
from .  import comms, keepalive, message

class Server:
    def __init__(self):
        self.clients: Dict[int, structs.Client] = {}
        self.address = None

        self._server_fut: concurrent.futures.Future = None
        
    def add_client(self, client: structs.Client):
        self.clients[client.id] = client

    def remove_client(self, client: structs.Client):
        del self.clients[client.id]

    async def start(self, address: Tuple[str,int]):
        self.server = await asyncio.start_server(self.handle_client, *address)

        addr = [sock.getsockname() for sock in self.server.sockets]
        if len(addr[0])!=2:
            addr[0], addr[1] = addr[1], addr[0]
        self.address = addr

        self._server_fut = async_thread.run(self.server.serve_forever())

    async def stop(self):
        # cancelling the serve_forever coroutine stops the server
        self._server_fut.cancel()
        await self.server.wait_closed()

    async def handle_client(self, reader: asyncio.streams.StreamReader, writer: asyncio.streams.StreamWriter):
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
