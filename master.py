import asyncio
import socket
import concurrent
import traceback

import sys
import pathlib
src_path = str(pathlib.Path(__file__).parent/"src")
if not src_path in sys.path:
    sys.path.append(src_path)
    
from labManager.utils import async_thread, network, structs, task



smb_server  = "srv2.humlab.lu.se"
domain      = "UW"
username    = "huml-dkn"


async def client_loop(id, reader, writer):
    type = None
    while type != network.message.Message.QUIT:
        try:
            type, msg = await network.comms.typed_receive(reader)
            if not type:
                # connection broken, close
                break

            match type:
                case network.message.Message.IDENTIFY:
                    await network.comms.typed_send(writer, network.message.Message.IDENTIFY, f'client{id}')
                case network.message.Message.INFO:
                    print(f'client {id} received: {msg}')

                case network.message.Message.TASK_CREATE:
                    async_thread.run(task.execute(msg['id'],msg['type'],msg['payload'],writer))

 
        except Exception as exc:
            tb_lines = traceback.format_exception(exc)
            print("".join(tb_lines))
            continue

    writer.close()

async def start_client(id):
    # 1. get interfaces we can work with
    interfaces = sorted(network.ifs.get_ifaces('192.168.1.0/24'))

    # 2. discover master
    # start SSDP client
    ssdp_client = network.ssdp.Client(address=interfaces[0], device_type=structs.SSDP_DEVICE_TYPE)
    await ssdp_client.start()
    # send search request and wait for reply
    responses = await ssdp_client.do_discovery()
    # stop SSDP client
    async_thread.run(ssdp_client.stop())
    # get ip and port for master from advertisement
    ip, _, port = responses[0].headers['HOST'].rpartition(':')
    port = int(port) # convert to integer

    # 3. connect to master
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((interfaces[0],0))
    await async_thread.loop.sock_connect(sock, (ip, port))
    reader, writer = await asyncio.open_connection(sock=sock)

    return async_thread.run(client_loop(id, reader, writer))

async def main():
    # 1. check user credentials, and list shares (projects) they have access to
    if False:
        from getpass import getpass
        password = getpass(f'Password for {domain}\{username}: ')
        try:
            smb = network.smb.SMBHandler(smb_server,username,domain,password)
        except (OSError, network.smb.SessionError) as exc:
            print(f'Error connecting as {domain}\{username} to {smb_server}: {exc}')
            shares = []
        else:
            shares = smb.list_shares()
            smb.close()
        print(shares)

    # 2. 
    # get interfaces we can work with
    interfaces = sorted(network.ifs.get_ifaces('192.168.1.0/24'))
    ## start servers
    # start server
    server = network.master.Server()
    async_thread.wait(server.start((interfaces[0], 0)))
    ip,port = server.address[0]

    # start SSDP server
    ssdp_server = network.ssdp.Server(
        address=interfaces[0],
        host_ip_port=(ip,port),
        usn="humlab-b055-master::"+structs.SSDP_DEVICE_TYPE,
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
    async_thread.run(network.comms.typed_send(server.clients[1].writer, network.message.Message.INFO, 'sup'))
    #mytask = task.create(task.Type.Shell_command, r"echo test")
    mytask = task.create(task.Type.Process_exec, r"ping localhost")
    async_thread.run(task.send(mytask, server.clients[1].writer))
    #mytask = task.create(task.Type.Python_module, r"pip list")
    #async_thread.run(task.send(mytask, server.clients[1].writer))
    #mytask = task.create(task.Type.Python_statement, r"import math;print(math.sin(1))")
    #async_thread.run(task.send(mytask, server.clients[1].writer))

    await asyncio.sleep(5)
    async_thread.run(server.broadcast(network.message.Message.QUIT))
        

    # wait for clients to finish
    for a in aas:
        a.result()
        
    # stop servers
    async_thread.run(ssdp_server.stop()).result()
    async_thread.run(server.stop()).result()

if __name__ == "__main__":
    async_thread.setup()
    asyncio.run(main())
    async_thread.cleanup()
    