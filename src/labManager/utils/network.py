import asyncio
import concurrent
import struct
import traceback
from typing import List, Tuple

from . import async_thread, keepalive, structs


SIZE_MESSAGE_FMT  = '!I'
SIZE_MESSAGE_SIZE = struct.calcsize(SIZE_MESSAGE_FMT)
async def read_with_length(reader: asyncio.streams.StreamReader) -> str:
    # protocol: first the size of a message is sent so 
    # receiver knows what to expect. Then the message itself
    # is sent
    try:
        try:
            msg_size = await reader.readexactly(SIZE_MESSAGE_SIZE)
        except asyncio.IncompleteReadError:
            # connection broken
            return None
        msg_size = struct.unpack(SIZE_MESSAGE_FMT, msg_size)[0]

        buf = ''
        left_over = msg_size
        while left_over>0:
            received = (await reader.read(min(4096, left_over))).decode('utf8')
            if not received:
                # connection broken
                return ''
            buf += received
            left_over = msg_size-len(buf)
        return buf

    except ConnectionError:
        return ''

async def receive_typed_message(reader: asyncio.streams.StreamReader) -> Tuple[structs.Message,str]:
    type    = await read_with_length(reader)
    if not type:
        return None,''

    message = await read_with_length(reader)
    
    return structs.Message.get(type), message

async def send_with_length(writer: asyncio.streams.StreamWriter, message: str) -> bool:
    try:
        to_send = message.encode('utf8')

        # first notify end point of message length
        writer.write(struct.pack(SIZE_MESSAGE_FMT, len(to_send)))

        # then send message, if anything
        if to_send:
            writer.write(to_send)

        await writer.drain()
        return True
    except ConnectionError:
        return False

async def send_typed_message(writer: asyncio.streams.StreamWriter, type: structs.Message, message: str=''):
    await send_with_length(writer,type.value)
    await send_with_length(writer,message)



class Server:
    def __init__(self):
        self.client_list: List[structs.Client] = []
        self.server_address = None

        self._server_fut: concurrent.futures.Future = None

    async def start(self, server_address: Tuple[str,int]):
        self.server = await asyncio.start_server(self.handle_client, *server_address)

        addr = [sock.getsockname() for sock in self.server.sockets]
        if len(addr[0])!=2:
            addr[0], addr[1] = addr[1], addr[0]
        self.server_address = addr
        print('serving on {}:{}'.format(*addr[0]))

        self._server_fut = async_thread.run(self.server.serve_forever())

        return addr

    def stop(self):
        # cancelling the serve_forever coroutine stops the server
        self._server_fut.cancel()

    async def handle_client(self, reader: asyncio.streams.StreamReader, writer: asyncio.streams.StreamWriter):
        keepalive.set(writer.get_extra_info('socket'))

        me = structs.Client(writer)
        self.client_list.append(me)

        # request info about client
        await send_typed_message(writer, structs.Message.IDENTIFY)
    
        # process incoming messages
        type = None
        while type != structs.Message.QUIT:
            try:
                type, message = await receive_typed_message(reader)
                if not type:
                    # connection broken, close
                    break

                match type:
                    case structs.Message.IDENTIFY:
                        me.name = message
                        print(f'setting name for {me.host}:{me.port} to: {message}')
                    case structs.Message.INFO:
                        print(f'{me.host}:{me.port}: {message}')
 
            except Exception as exc:
                tb_lines = traceback.format_exception(exc)
                print("".join(tb_lines))
                continue

        writer.close()

        # remove from client list
        self.client_list = [c for c in self.client_list if c.name!=me.name]

    async def broadcast(self, type: structs.Message, message: str=''):
        for c in self.client_list:
            await send_typed_message(c.writer, type, message)