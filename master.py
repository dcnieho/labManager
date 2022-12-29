import asyncio
import concurrent

import sys
import pathlib
src_path = str(pathlib.Path(__file__).parent/"src")
if not src_path in sys.path:
    sys.path.append(src_path)
    
from labManager.utils import async_thread, config, network, structs

#import logging
#logging.basicConfig(level=logging.DEBUG)



async def main():
    from getpass import getpass
    # 1. check user credentials, and list shares (projects) they have access to
    if False:
        username = getpass(f'Username for logging into {config.master["SMB"]["server"]} in domain {config.master["SMB"]["domain"]}: ')
        password = getpass(f'Password for {config.master["SMB"]["domain"]}\{username}: ')
        try:
            smb = network.smb.SMBHandler(config.master["SMB"]["server"],username,config.master["SMB"]["domain"],password)
        except (OSError, network.smb.SessionError) as exc:
            print(f'Error connecting as {config.master["SMB"]["domain"]}\{username} to {config.master["SMB"]["server"]}: {exc}')
            shares = []
        else:
            shares = smb.list_shares()
            smb.close()
        print(shares)

    # 2. log into toems server
    if False:
        toems = network.toems.Client(config.master['toems']['server'], config.master['toems']['port'], protocol='http')
        toems_username = getpass(f'Username for logging into toems server {config.master["toems"]["server"]}: ')
        toems_password = getpass(f'Password for logging in with toems user {toems_username}: ')
        await toems.connect(toems_username, toems_password)
    
        image_list = await toems.image_get()
        image = await toems.image_get(2)
        print(image)

    # 3. start servers for listening to clients
    # get interfaces we can work with
    if_ips,_ = network.ifs.get_ifaces(config.master['network'])
    # start server to connect with clients
    server = network.master.Server()
    server.load_known_clients(config.master['clients'])
    async_thread.wait(server.start((if_ips[0], 0)))
    ip,port = server.address[0]

    # start SSDP server to advertise this server
    ssdp_server = network.ssdp.Server(
        address=if_ips[0],
        host_ip_port=(ip,port),
        usn="humlab-b055-master::"+structs.SSDP_DEVICE_TYPE,
        device_type=structs.SSDP_DEVICE_TYPE,
        allow_loopback=True)
    async_thread.wait(ssdp_server.start())

    # serve indefinitely....
    await asyncio.sleep(5)

    # stop servers
    async_thread.run(ssdp_server.stop()).result()
    async_thread.run(server.stop()).result()

if __name__ == "__main__":
    async_thread.setup()
    asyncio.run(main())
    async_thread.cleanup()
    