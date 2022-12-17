import asyncio
import socket
import threading
import concurrent
import traceback
import typing

import sys
import pathlib
src_path = str(pathlib.Path(__file__).parent/"src")
if not src_path in sys.path:
    sys.path.append(src_path)

import labManager.utils as utils
import labManager.utils.structs as structs
import labManager.utils.network as network

# to allow clients to discover server:
# Both connect to muticast on their configged subnet
# server sends periodic (1s?) announcements
# client stops listening once server found
# or look into what zeroconf does



client_list: typing.List[structs.Client] = []



async def handle_client(reader, writer):
    global client_list
    utils.keepalive.set(writer.get_extra_info('socket'))

    me = structs.Client(writer)
    client_list.append(me)

    # request info about client
    await network.send_typed_message(writer, structs.Message.IDENTIFY)
    
    # process incoming messages
    type = None
    while type != structs.Message.QUIT:
        try:
            type, message = await network.receive_typed_message(reader)
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
        await network.send_typed_message(c.writer, type, message)

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
            type, message = await network.receive_typed_message(reader)
            if not type:
                # connection broken, close
                break

            match type:
                case structs.Message.IDENTIFY:
                    await network.send_typed_message(writer, structs.Message.IDENTIFY, f'client{id}')
                case structs.Message.INFO:
                    print(f'client {id} received: {message}')
 
        except Exception as exc:
            tb_lines = traceback.format_exception(exc)
            print("".join(tb_lines))
            continue

    writer.close()

async def start_client(loop, ip, port, id):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    await loop.sock_connect(sock, (ip, port))
    reader, writer = await asyncio.open_connection(sock=sock)

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
        asyncio.run_coroutine_threadsafe(start_client(loop, ip, port, 1),loop),
        asyncio.run_coroutine_threadsafe(start_client(loop, ip, port, 2),loop),
        asyncio.run_coroutine_threadsafe(start_client(loop, ip, port, 3),loop)
    ]

    # wait till clients have started, get futures to their processing loop tasks
    aas = [f.result() for f in concurrent.futures.as_completed(aas)]

    # send some messages to clients
    asyncio.run_coroutine_threadsafe(network.send_typed_message(client_list[1].writer, structs.Message.INFO, 'sup'),loop)
    asyncio.run_coroutine_threadsafe(broadcast(structs.Message.QUIT),loop)
        

    # wait for clients to finish
    for a in aas:
        a.result()

if __name__ == "__main__":
    setup_async()
    asyncio.run(main())
        
    