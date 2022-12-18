import asyncio
import concurrent
import traceback
from typing import List, Tuple

from .. import async_thread, structs
from .  import comms, constants, keepalive

class Server:
    def __init__(self):
        self.client_list: List[structs.Client] = []
        self.address = None

        self._server_fut: concurrent.futures.Future = None

    async def start(self, address: Tuple[str,int]):
        self.server = await asyncio.start_server(self.handle_client, *address)

        addr = [sock.getsockname() for sock in self.server.sockets]
        if len(addr[0])!=2:
            addr[0], addr[1] = addr[1], addr[0]
        self.address = addr

        self._server_fut = async_thread.run(self.server.serve_forever())

    def stop(self):
        # cancelling the serve_forever coroutine stops the server
        self._server_fut.cancel()

    async def handle_client(self, reader: asyncio.streams.StreamReader, writer: asyncio.streams.StreamWriter):
        keepalive.set(writer.get_extra_info('socket'))

        me = structs.Client(writer)
        self.client_list.append(me)

        # request info about client
        await comms.send_typed_message(writer, constants.Message.IDENTIFY)
    
        # process incoming messages
        type = None
        while type != constants.Message.QUIT:
            try:
                type, message = await comms.receive_typed_message(reader)
                if not type:
                    # connection broken, close
                    break

                match type:
                    case constants.Message.IDENTIFY:
                        me.name = message
                        print(f'setting name for {me.host}:{me.port} to: {message}')
                    case constants.Message.INFO:
                        print(f'{me.host}:{me.port}: {message}')
 
            except Exception as exc:
                tb_lines = traceback.format_exception(exc)
                print("".join(tb_lines))
                continue

        writer.close()

        # remove from client list
        self.client_list = [c for c in self.client_list if c.name!=me.name]

    async def broadcast(self, type: constants.Message, message: str=''):
        for c in self.client_list:
            await comms.send_typed_message(c.writer, type, message)
