import asyncio
import concurrent

import sys
import pathlib
src_path = str(pathlib.Path(__file__).parent/"src")
if not src_path in sys.path:
    sys.path.append(src_path)
    
from labManager.utils import async_thread, network, structs, task

#import logging
#logging.basicConfig(level=logging.DEBUG)



smb_server  = "srv2.humlab.lu.se"
domain      = "UW"
username    = "huml-dkn"
my_network  = '192.168.1.0/24'

num_clients = 3


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

    # 2. start servers
    # get interfaces we can work with
    interfaces = network.ifs.get_ifaces(my_network)
    # start server to connect with clients
    server = network.master.Server()
    async_thread.wait(server.start((interfaces[0], 0)))
    ip,port = server.address[0]

    # start SSDP server to advertise this server
    ssdp_server = network.ssdp.Server(
        address=interfaces[0],
        host_ip_port=(ip,port),
        usn="humlab-b055-master::"+structs.SSDP_DEVICE_TYPE,
        device_type=structs.SSDP_DEVICE_TYPE,
        allow_loopback=True)
    async_thread.wait(ssdp_server.start())

    # serve indefinitely....


    # stop servers
    async_thread.run(ssdp_server.stop()).result()
    async_thread.run(server.stop()).result()

if __name__ == "__main__":
    async_thread.setup()
    asyncio.run(main())
    async_thread.cleanup()
    