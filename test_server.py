import asyncio
import socket
import struct
import threading
import concurrent
import traceback

import utils
import structs
import typing

# to allow clients to discover server:
# Both connect to muticast on their configged subnet
# server sends periodic (1s?) announcements
# client stops listening once server found
# or look into what zeroconf does



SIZE_MESSAGE_FMT  = '!I'
SIZE_MESSAGE_SIZE = struct.calcsize(SIZE_MESSAGE_FMT)
client_list: typing.List[structs.Client] = []

async def read_with_length(reader) -> str:
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

async def receive_typed_message(reader) -> typing.Tuple[structs.Message,str]:
    type    = await read_with_length(reader)
    if not type:
        return None,''

    message = await read_with_length(reader)
    
    return structs.Message.get(type), message

async def send_with_length(writer, message) -> bool:
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

async def send_typed_message(writer, type: structs.Message, message=''):
    await send_with_length(writer,type.value)
    await send_with_length(writer,message)

async def handle_client(reader, writer):
    global client_list
    utils.set_keepalive(writer.get_extra_info('socket'))

    me = structs.Client(writer)
    client_list.append(me)

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
    client_list = [c for c in client_list if c.name!=me.name]

async def broadcast(type, message=''):
    for c in client_list:
        await send_typed_message(c.writer, type, message)

async def run_server(server_address):
    server = await asyncio.start_server(handle_client, *server_address)

    server_addr = [sock.getsockname() for sock in server.sockets]
    if len(server_addr[0])!=2:
        server_addr[0],server_addr[1] = server_addr[1],server_addr[0]
    print('serving on {}:{}'.format(*server_addr[0]))

    asyncio.run_coroutine_threadsafe(server.serve_forever(), loop)

    return server_addr


async def client_loop(id, reader, writer):
    type = None
    while type != structs.Message.QUIT:
        try:
            type, message = await receive_typed_message(reader)
            if not type:
                # connection broken, close
                break

            match type:
                case structs.Message.IDENTIFY:
                    await send_typed_message(writer, structs.Message.IDENTIFY, f'client{id}')
                case structs.Message.INFO:
                    print(f'client {id} received: {message}')
 
        except Exception as exc:
            tb_lines = traceback.format_exception(exc)
            print("".join(tb_lines))
            continue

    writer.close()

async def start_client(loop, ip, port, id, message):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    await loop.sock_connect(sock, (ip, port))
    reader, writer = await asyncio.open_connection(sock=sock)

    await send_typed_message(writer, structs.Message.INFO, message)

    return asyncio.run_coroutine_threadsafe(client_loop(id, reader, writer), loop)

loop = None
def setup_async():
    global loop
    def run_loop(loop: asyncio.AbstractEventLoop):
        asyncio.set_event_loop(loop)
        try:
            loop.run_forever()
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=run_loop, args=(loop,), daemon=True)
    thread.start()

async def main():
    # start server
    server_addr = asyncio.run_coroutine_threadsafe(run_server(("localhost", 0)), loop)
    ip,port = server_addr.result()[0]
    
    # start clients
    aas = [
        asyncio.run_coroutine_threadsafe(start_client(loop, ip, port, 1, "Hello World 1, more text"),loop),
        asyncio.run_coroutine_threadsafe(start_client(loop, ip, port, 2, "Hello World 2"),loop),
        asyncio.run_coroutine_threadsafe(start_client(loop, ip, port, 3, "Hello World 3"),loop)
    ]

    # wait till clients have started, get futures to their processing loop tasks
    aas = [f.result() for f in concurrent.futures.as_completed(aas)]

    # send some messages to clients
    asyncio.run_coroutine_threadsafe(send_typed_message(client_list[1].writer, structs.Message.INFO, 'sup'),loop)
    asyncio.run_coroutine_threadsafe(broadcast(structs.Message.QUIT),loop)
        

    # wait for clients to finish
    for a in aas:
        a.result()

if __name__ == "__main__":
    setup_async()
    asyncio.run(main())
        
    