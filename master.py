import asyncio
import concurrent

import sys
import pathlib
src_path = str(pathlib.Path(__file__).parent/"src")
if not src_path in sys.path:
    sys.path.append(src_path)
    
from labManager.utils import async_thread, network, structs, task



smb_server  = "srv2.humlab.lu.se"
domain      = "UW"
username    = "huml-dkn"
my_network  = '192.168.1.0/24'


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
    clients = [network.client.Client(my_network) for _ in range(3)]
    aas     = [async_thread.run(c.start()) for c in clients]

    # wait till clients have started, get futures to their processing loop tasks
    aas = [f.result() for f in concurrent.futures.as_completed(aas)]

    # send some messages to clients
    async_thread.run(network.comms.typed_send(server.clients[1].writer, network.message.Message.INFO, 'sup'))
    async_thread.run(server.run_task(task.Type.Process_exec, r"ping localhost", '*'))

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
    