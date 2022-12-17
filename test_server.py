import asyncio
import socket
import concurrent
import traceback
import typing

import sys
import pathlib
src_path = str(pathlib.Path(__file__).parent/"src")
if not src_path in sys.path:
    sys.path.append(src_path)
    
from labManager.utils import async_thread, structs, network

# to allow clients to discover server:
# Both connect to muticast on their configged subnet
# server sends periodic (1s?) announcements
# client stops listening once server found
# or look into what zeroconf does






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

async def start_client(ip, port, id):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    await async_thread.loop.sock_connect(sock, (ip, port))
    reader, writer = await asyncio.open_connection(sock=sock)

    return async_thread.run(client_loop(id, reader, writer))

async def main():
    # start server
    server = network.Server()
    async_thread.wait(server.start(("localhost", 0)))
    ip,port = server.address[0]
    
    # start clients
    aas = [
        async_thread.run(start_client(ip, port, 1)),
        async_thread.run(start_client(ip, port, 2)),
        async_thread.run(start_client(ip, port, 3))
    ]

    # wait till clients have started, get futures to their processing loop tasks
    aas = [f.result() for f in concurrent.futures.as_completed(aas)]

    # send some messages to clients
    async_thread.run(network.send_typed_message(server.client_list[1].writer, structs.Message.INFO, 'sup'))
    async_thread.run(server.broadcast(structs.Message.QUIT))
        

    # wait for clients to finish
    for a in aas:
        a.result()

    server.stop()

if __name__ == "__main__":
    async_thread.setup()
    asyncio.run(main())
    async_thread.cleanup()
        
    