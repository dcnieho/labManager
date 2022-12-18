import asyncio
import socket
import concurrent
import traceback

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
    while type != network.constants.Message.QUIT:
        try:
            type, message = await network.comms.receive_typed_message(reader)
            if not type:
                # connection broken, close
                break

            match type:
                case network.constants.Message.IDENTIFY:
                    await network.comms.send_typed_message(writer, network.constants.Message.IDENTIFY, f'client{id}')
                case network.constants.Message.INFO:
                    print(f'client {id} received: {message}')
 
        except Exception as exc:
            tb_lines = traceback.format_exception(exc)
            print("".join(tb_lines))
            continue

    writer.close()

async def start_client(id):
    # 1. discover master
    # start SSDP client
    ssdp_client = network.ssdp.Client(structs.SSDP_DEVICE_TYPE)
    await ssdp_client.start()
    # send search request and wait for reply
    responses = await ssdp_client.do_discovery()
    # stop SSDP client
    async_thread.run(ssdp_client.stop())
    # get ip and port for master from advertisement
    ip, _, port = responses[0].headers['HOST'].rpartition(':')
    port = int(port) # convert to integer

    # connect to master
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    await async_thread.loop.sock_connect(sock, (ip, port))
    reader, writer = await asyncio.open_connection(sock=sock)

    return async_thread.run(client_loop(id, reader, writer))

async def main():
    ## start servers
    # start server
    server = network.manager.Server()
    async_thread.wait(server.start(("localhost", 0)))
    ip,port = server.address[0]

    # start SSDP server
    ssdp_server = network.ssdp.Server(
        (ip,port),
        "humlab-b055-master::"+structs.SSDP_DEVICE_TYPE,
        device_type=structs.SSDP_DEVICE_TYPE,
        allow_loopback=True)
    async_thread.wait(ssdp_server.start())
    
    # start clients
    aas = [
        async_thread.run(start_client(1)),
        async_thread.run(start_client(2)),
        async_thread.run(start_client(3))
    ]

    # wait till clients have started, get futures to their processing loop tasks
    aas = [f.result() for f in concurrent.futures.as_completed(aas)]

    # send some messages to clients
    async_thread.run(network.comms.send_typed_message(server.client_list[1].writer, network.constants.Message.INFO, 'sup'))
    async_thread.run(server.broadcast(network.constants.Message.QUIT))
        

    # wait for clients to finish
    for a in aas:
        a.result()
        
    # stop servers
    async_thread.run(ssdp_server.stop()).result()
    server.stop()

if __name__ == "__main__":
    async_thread.setup()
    asyncio.run(main())
    async_thread.cleanup()
    