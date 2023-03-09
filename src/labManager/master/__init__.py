import asyncio
import aiofile
import traceback
import sys
from typing import Dict, List, Tuple

from ..utils import async_thread, config, eye_tracker, message, network, structs, task


# main function for independently running master
# does not return until master has closed down
# duration parameter only applies to command-line master (use_GUI==False)
def run(use_GUI: bool = True, duration: float = None):
    # if we want a GUI, first check we have that functionality installed
    if use_GUI:
        from .. import _config
        if not _config.HAS_GUI:
            raise RuntimeError('You must install labManager with the [GUI] extra if you wish to use the GUI. Required dependencies for the GUI not available...')

    # set up thread for running asyncs
    async_thread.setup()

    # run actual master
    if use_GUI:
        do_run_GUI()
    else:
        asyncio.run(do_run(duration))

    # clean up
    async_thread.cleanup()

# coroutine that runs command-line master
async def do_run(duration: float = None):
    from getpass import getpass
    username = input(f'Username: ')
    password = getpass(f'Password: ')
    server = Master()
    server.load_known_clients(config.master['clients'])
    await server.login(username, password)
    print('You have access to the following projects, which would you like to use?')
    for p in server.projects:
        print(f'  {p}')
    project = input(f'Project: ')
    await server.set_project(project)

    # 2. check we also have share access
    access = server.has_share_access()

    # 3. log into toems server
    image_list = await server.get_images()
    comp_list = await server.get_computers()

    #image_start = await server.deploy_image('station_base', ['STATION01'])
    #new = await server.create_image('test')
    image_start = await server.upload_computer_to_image('STATION01','test')
    # image_tasks = await toems.imaging_task_get()

    # 4. start servers for listening to clients
    # get interfaces we can work with
    if_ips,_ = network.ifs.get_ifaces(config.master['network'])
    # start server to connect with stations
    await server.start_server((if_ips[0], 0))

    # run
    if not duration:
        # wait forever
        await asyncio.Event().wait()
    else:
        await asyncio.sleep(duration)

    # stop servers
    await server.stop_server()

# run GUI master
def do_run_GUI():
    from . import GUI
    if getattr(sys, "frozen", False) and "nohide" not in sys.argv:
        import ctypes
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)

    gui = GUI.MainGUI()
    # returns when GUI closed
    gui.run()


class Master:
    def __init__(self):
        ### user interface
        # credentials
        self.username = None
        self.password = None
        # all projects user has access to and selected project
        self.projects = []
        self.project  = None
        self.has_share_access = False

        # connections to servers
        self.admin: network.admin_conn.Client = None
        self.toems: network.toems.Client = None

        # server
        self.address = None
        self.ssdp_server: network.ssdp.Server = None

        self.clients: Dict[int, structs.Client] = {}
        self.known_clients: Dict[int, structs.KnownClient] = {}

        self.task_groups: Dict[int, task.TaskGroup] = {}

    async def login(self, username: str, password: str):
        # clean up old session, if any
        self.logout()

        # check user credentials, and list projects they have access to
        self.admin = network.admin_conn.Client(config.master['admin']['server'], config.master['admin']['port'])
        self.projects = await self.admin.login(username, password)
        self.username, self.password = username, password

    def logout(self):
        self.unset_project()
        self.username, self.password = None, None
        self.projects = []
        if self.admin is not None:
            self.admin = None

    async def set_project(self, project: str):
        if project not in self.projects:
            raise ValueError(f'project "{project}" not recognized, choose one of the projects you have access to: {self.projects}')

        if project == self.project:
            return

        # we got a different project, unload old
        if self.toems is not None:
            self.toems = None

        # set new project
        self.project = project
        self.admin.set_project(self.project)

        # check SMB access
        self.has_share_access = _SMB_get_shares(self.admin.user, self.password, self.project)

        # log into toems server
        await self.admin.prep_toems()
        self.toems = network.toems.Client(config.master['toems']['server'], config.master['toems']['port'], protocol='http')
        await self.toems.connect(self.username, self.password)

    def unset_project(self):
        if self.toems is not None:
            self.toems = None
        self.project = None
        self.has_share_access = False
        if self.admin is not None:
            self.admin.unset_project()


    async def get_computers(self):
        return await self.toems.computer_get(filter_list=[c['name'] for c in config.master['clients']])

    async def get_images(self):
        return await self.toems.image_get(project=self.project, project_format=config.master['toems']['images']['format'])

    async def create_image(self, name: str, description: str|None = None):
        return await self.admin.create_image(name, description)

    async def delete_image(self, name: str):
        image_id = (await self.toems.image_get(name))['Id']
        return await self.admin.delete_image(image_id)

    async def deploy_image(self, image: str, computers: List[str]):
        image_id = (await self.toems.image_get(image))['Id']

        if isinstance(computers,str):
            computers = [computers]
        comp_ids = [(await self.toems.computer_get(c))['Id'] for c in computers]
        for c in comp_ids:
            resp = await self.admin.apply_image(image_id, c)
            if not resp['Success']:
                raise RuntimeError(f"can't deploy: failed to apply image to computer ({resp['ErrorMessage']})")

        resp = await self.toems.computer_deploy(image_id, comp_ids)
        if not 'Success' in resp['Value']:
            raise RuntimeError(f"can't deploy: failed to start task ({resp['ErrorMessage']})")

    async def upload_computer_to_image(self, computer: str, image: str):
        # we can only ever upload to an image belonging to this project, so check it is a project image
        if not image.startswith(self.project+'_'):
            image = self.project+'_'+image
        im = await self.toems.image_get(image)
        if im is None:
            raise RuntimeError(f"can't upload: image with name '{image}' not found")
        image_id = im['Id']

        comp = await self.toems.computer_get(computer)
        if comp is None:
            raise RuntimeError(f"can't upload: computer with name '{computer}' not found")
        comp_id  = comp['Id']
        resp = await self.admin.apply_image(image_id, comp_id)
        if not resp['Success']:
            raise RuntimeError(f"can't upload: failed to apply image to computer ({resp['ErrorMessage']})")

        resp = await self.toems.computer_upload(comp_id, image_id)
        if not 'Success' in resp['Value']:
            raise RuntimeError(f"can't upload: failed to start task ({resp['ErrorMessage']})")


    def _add_client(self, client: structs.Client):
        self.clients[client.id] = client

    def _remove_client(self, client: structs.Client):
        self._remove_known_client(client)
        del self.clients[client.id]

    def load_known_clients(self, known_clients: List[Tuple[str,str]]):
        for client in known_clients:
            kc = structs.KnownClient(client['name'], client['MAC'])
            self.known_clients[kc.id] = kc

    def _find_or_add_known_client(self, client: structs.Client):
        for id in self.known_clients:
            if self.known_clients[id].MAC in client.MACs:
                client.known_client = self.known_clients[id]
                self.known_clients[id].client = client
                return

        # client not known, add
        kc = structs.KnownClient(client['name'], client['MAC'], client=client)
        self.known_clients[kc.id] = kc
        client.known_client = self.known_clients[kc.id]

    def _remove_known_client(self, client: structs.Client):
        if client.known_client:
            client.known_client.client = None
        client.known_client = None

    async def start_server(self, local_addr: Tuple[str,int], start_ssdp_advertise=True):
        self.server = await asyncio.start_server(self._handle_client, *local_addr)

        addr = [sock.getsockname() for sock in self.server.sockets]
        if len(addr[0])!=2:
            addr[0], addr[1] = addr[1], addr[0]
        self.address = addr

        # should already have started serving in asyncio.start_server, but to be save and sure:
        await self.server.start_serving()

        # start SSDP server if wanted
        if start_ssdp_advertise:
            # start SSDP server to advertise this server
            self.ssdp_server = network.ssdp.Server(
                address=local_addr[0],
                host_ip_port=self.address[0],
                usn="humlab-b055-master::"+config.master['SSDP']['device_type'],
                device_type=config.master['SSDP']['device_type'],
                allow_loopback=True)
            await self.ssdp_server.start()

    async def stop_server(self):
        if self.ssdp_server is not None:
            await self.ssdp_server.stop()

        self.server.close()
        await self.server.wait_closed()

    async def _handle_client(self, reader: asyncio.streams.StreamReader, writer: asyncio.streams.StreamWriter):
        network.keepalive.set(writer.get_extra_info('socket'))

        me = structs.Client(writer)
        self._add_client(me)

        # request info about client
        await network.comms.typed_send(writer, message.Message.IDENTIFY)

        # process incoming messages
        type = None
        while type != message.Message.QUIT:
            try:
                type, msg = await network.comms.typed_receive(reader)
                if not type:
                    # connection broken, close
                    break

                match type:
                    case message.Message.IDENTIFY:
                        me.name = msg['name']
                        me.MACs = msg['MACs']
                        self._find_or_add_known_client(me)
                    case message.Message.INFO:
                        print(f'{me.host}:{me.port}: {msg}')

                    case message.Message.ET_ATTR_UPDATE:
                        if not me.eye_tracker:
                            me.eye_tracker = eye_tracker.EyeTracker()
                        if 'attributes' in msg:
                            # this is a timestamped update message
                            # add to eye-tracker events for this client
                            # TODO
                            # update attributes
                            eye_tracker.update_attributes(me.eye_tracker, msg['attributes'])
                        else:
                            eye_tracker.update_attributes(me.eye_tracker, msg)

                    case message.Message.TASK_OUTPUT:
                        mytask = me.tasks[msg['task_id']]
                        match msg['stream_type']:
                            case task.StreamType.STDOUT:
                                mytask.stdout += msg['output']
                            case task.StreamType.STDERR:
                                mytask.stderr += msg['output']
                    case message.Message.TASK_UPDATE:
                        mytask = me.tasks[msg['task_id']]
                        mytask.status = msg['status']
                        if 'return_code' in msg:
                            mytask.return_code = msg['return_code']

                    case _:
                        print(f'got unhandled type {type.value}, message: {msg}')

            except Exception as exc:
                tb_lines = traceback.format_exception(exc)
                print("".join(tb_lines))
                continue

        writer.close()

        # remove from client list
        self._remove_client(me)

    async def broadcast(self, type: message.Message, message: str=''):
        for c in self.clients:
            await network.comms.typed_send(self.clients[c].writer, type, message)

    async def run_task(self,
                       type: task.Type,
                       payload: str,
                       clients: List[int] | str,
                       payload_type='cmd_or_script',
                       cwd: str=None,
                       env: dict=None,
                       interactive=False):
        # clients has a special value '*' which means all clients
        if clients=='*':
            clients = [c for c in self.clients]

        # handle payload
        match payload_type:
            case 'cmd_or_script':
                pass
            case 'file':
                async with aiofile.async_open(payload, 'rt') as afp:
                    payload = await afp.read()

        # make task group
        task_group = task.create_group(type, payload, clients, cwd=cwd, env=env, interactive=interactive)
        self.task_groups[task_group.id] = task_group

        # start tasks
        coros = []
        for c in task_group.task_refs:
            mytask = task_group.task_refs[c]
            # add to client task list
            self.clients[c].tasks[mytask.id] = mytask
            # send
            coros.append(task.send(mytask, self.clients[c].writer))

        await asyncio.gather(*coros)



def _SMB_get_shares(user, password, project=None):
    # figure out domain from user, default to configured
    domain = config.master["SMB"]["domain"]
    if '\\' in user['full_name']:
        dom, _ = user['full_name'].split('\\', maxsplit=1)
        if dom:
            domain = dom
    try:
        smb_hndl = network.smb.SMBHandler(config.master["SMB"]["server"], user['name'], domain, password)
    except (OSError, network.smb.SessionError) as exc:
        print(f'Error connecting as {domain}\{user["name"]} to {config.master["SMB"]["server"]}: {exc}')
        shares = []
    else:
        shares = smb_hndl.list_shares(matching=config.master["SMB"]["projects"]["format"], remove_trailing=config.master["SMB"]["projects"]["remove_trailing"], contains=project)

    return shares