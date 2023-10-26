import pkg_resources
import asyncio
import aiofile
import traceback
import sys
import threading
from typing import Dict, List, Tuple

from labManager.common import async_thread, config, eye_tracker, message, structs, task
from labManager.common.network import admin_conn, comms, ifs, keepalive, smb, ssdp, toems

def _check_has_GUI():
    if 'imgui-bundle' not in {pkg.key for pkg in pkg_resources.working_set}:
        raise RuntimeError('You must install labManager-master with the [GUI] extra if you wish to use the GUI. Required dependencies for the GUI not available...')


# main function for independently running master
# does not return until master has closed down
# duration parameter only applies to command-line master (use_GUI==False)
def run(use_GUI: bool = True, duration: float = None):
    # if we want a GUI, first check we have that functionality installed
    if use_GUI:
        _check_has_GUI()

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
    master = Master()
    master.load_known_clients(config.master['clients'])
    await master.login(username, password)
    print('You have access to the following projects, which would you like to use?')
    for p,pn in master.projects.items():
        if pn==p:
            print(f'  {p}')
        else:
            print(f'  {p} ({pn})')
    project = input(f'Project: ')
    await master.set_project(project)

    # start server to connect with stations
    await master.start_server()

    # run
    if not duration:
        # wait forever
        await asyncio.Event().wait()
    else:
        await asyncio.sleep(duration)

    # stop servers
    await master.stop_server()

# run GUI master
def do_run_GUI():
    _check_has_GUI()
    from labManager.GUI import master as master_GUI
    if getattr(sys, "frozen", False) and "nohide" not in sys.argv:
        import ctypes
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)

    gui = master_GUI.MainGUI()
    # returns when GUI closed
    gui.run()


class Master:
    def __init__(self):
        ### user interface
        # credentials
        self.username = None
        self.password = None
        # all projects user has access to and selected project
        self.projects = Dict[str, str]
        self.project  = None
        self.has_share_access = False

        # connections to servers
        self.admin: admin_conn.Client = None
        self.toems: toems.Client = None

        # servers
        self.address = None
        self.server  = None
        self.ssdp_server: ssdp.Server = None

        self.clients: Dict[int, structs.Client] = {}
        self.known_clients: Dict[int, structs.KnownClient] = {}
        self.known_clients_lock = threading.Lock()
        self.client_et_events: Dict[int, List[Dict]] = {}

        self.task_groups: Dict[int, task.TaskGroup] = {}

    async def login(self, username: str, password: str):
        # clean up old session, if any
        self.logout()

        # check user credentials, and list projects they have access to
        self.admin = admin_conn.Client(config.master['admin']['server'], config.master['admin']['port'])
        await self.admin.login(username, password)
        self.username, self.password = username, password

        # prep user's projects
        self.load_projects()

    def logout(self):
        self.unset_project()
        self.username, self.password = None, None
        self.projects = {}
        if self.admin is not None:
            self.admin = None

    def load_projects(self):
        projects = self.admin.get_projects()
        names_to_override = []
        if 'projects' in config.master:
            names_to_override = [k for k in config.master['projects']['name_table']]
        for p in projects:
            project_display_name = p
            if p in names_to_override:
                project_display_name = config.master['projects']['name_table'][p]
            self.projects[p] = project_display_name

    async def set_project(self, project: str):
        if project not in self.projects:
            # make nice error message
            projects = []
            for p,pn in self.projects.items():
                if pn==p:
                    projects.append(p)
                else:
                    projects.append(f'{p} ({pn})')
            projects = "\n  ".join(projects)
            raise ValueError(f'project "{project}" not recognized, choose one of the projects you have access to: \n  {projects}')

        if project == self.project:
            return

        # we got a different project, unload old
        if self.toems is not None:
            self.toems = None

        # set new project
        self.admin.set_project(project)

        # check SMB access
        self.has_share_access = _SMB_get_shares(self.admin.user, self.password, project)

        # log into toems server
        await self.admin.prep_toems()
        self.toems = toems.Client(config.master['toems']['server'], config.master['toems']['port'], protocol='http')
        await self.toems.connect(self.username, self.password)
        self.project = project

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

    async def get_image_size(self, name_or_id: int|str):
        return await self.toems.image_get_server_size(name_or_id)

    async def create_image(self, name: str, description: str|None = None):
        return await self.admin.create_image(name, description)

    async def update_image(self, name: str, updates):
        image_id = (await self.toems.image_get(name))['Id']
        return await self.admin.update_image(image_id, updates)

    async def delete_image(self, name: str):
        image_id = (await self.toems.image_get(name))['Id']
        return await self.admin.delete_image(image_id)

    async def deploy_image(self, image: str, computers: List[str]):
        image_id = (await self.toems.image_get(image))['Id']

        if isinstance(computers,str):
            computers = [computers]
        comps = [(await self.toems.computer_get(c)) for c in computers]
        comp_ids = [c['Id'] for c in comps if c is not None]
        if not comp_ids:
            raise RuntimeError(f"can't deploy: the selected computers are not found or not known to Toems")
        for c in comp_ids:
            resp = await self.admin.apply_image(image_id, c)
            if not resp['Success']:
                raise RuntimeError(f"can't deploy: failed to apply image to computer ({resp['ErrorMessage']})")

        resp = await self.toems.computer_deploy(image_id, comp_ids)
        if not resp['Success']:
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
            raise RuntimeError(f"can't upload: computer with name '{computer}' not found or not known to Toems")
        comp_id  = comp['Id']
        resp = await self.admin.apply_image(image_id, comp_id)
        if not resp['Success']:
            raise RuntimeError(f"can't upload: failed to apply image to computer ({resp['ErrorMessage']})")

        resp = await self.toems.computer_upload(comp_id, image_id)
        if not 'Success' in resp['Value']:
            raise RuntimeError(f"can't upload: failed to start task ({resp['ErrorMessage']})")

    async def get_active_imaging_tasks(self, image_id: int|None = None):
        resp = await self.toems.imaging_tasks_get_active()
        # info about what image the task concerns is contained in the Computer dict under ImageId

        out = []
        for r in resp:
            item = {}
            item['TaskId'] = r['Id']    # needed for cancelling
            item['ImageId'] = r['Computer']['ImageId']
            if image_id is not None and item['ImageId']!=image_id:
                continue
            item['ComputerName'] = r['Computer']['Name']
            item['ComputerId'] = r['ComputerId']
            item['Type'] = r['Type']

            # legend: status TaskCreated = 0, WaitingForLogin = 1, CheckedIn = 2, InImagingQueue = 3, Imaging = 4
            # https://github.com/jdolny/Toems/blob/master/Toems-Common/Enum/EnumTaskStatus.cs
            match r['Status']:
                case 0:
                    item['Status'] = 'TaskCreated'
                case 1:
                    item['Status'] = 'WaitingForLogin'
                case 2:
                    item['Status'] = 'CheckedIn'
                case 3:
                    item['Status'] = f'InQueue (Position {r["QueuePosition"]})'
                case 4:
                    item['Status'] = 'Imaging'
            item['Partition'] = r['Partition'] if r['Partition'] is not None else ''
            item['Elapsed'] = r['Elapsed'] if r['Elapsed'] is not None else ''
            item['Remaining'] = r['Remaining'] if r['Remaining'] is not None else ''
            item['Completed'] = r['Completed'] if r['Completed'] is not None else ''
            item['Rate'] = r['Rate'] if r['Rate'] is not None else ''

            out.append(item)

        return out

    async def delete_active_imaging_task(self, task_id: int):
        resp = await self.toems.imaging_tasks_delete_active(task_id)
        if not 'Success' in resp or not resp['Success']:
            raise RuntimeError(f"can't delete active image task: failed because: {resp['ErrorMessage']}")


    def _add_client(self, client: structs.Client):
        self.clients[client.id] = client
        self.client_et_events[client.id] = []

    def _remove_client(self, client: structs.Client):
        self._remove_known_client(client)
        del self.clients[client.id]
        del self.client_et_events[client.id]

    def load_known_clients(self, known_clients: List[Tuple[str,str]]):
        with self.known_clients_lock:
            for client in known_clients:
                kc = structs.KnownClient(client['name'], client['MAC'])
                self.known_clients[kc.id] = kc

    def _find_or_add_known_client(self, client: structs.Client):
        with self.known_clients_lock:
            for id in self.known_clients:
                if self.known_clients[id].MAC in client.MACs:
                    client.known_client = self.known_clients[id]
                    self.known_clients[id].client = client
                    return

            # client not known, add
            kc = structs.KnownClient(client.name, client.MACs[0], client=client)
            self.known_clients[kc.id] = kc
            client.known_client = self.known_clients[kc.id]

    def _remove_known_client(self, client: structs.Client):
        if client.known_client:
            client.known_client.client = None
        client.known_client = None

    async def start_server(self, local_addr: Tuple[str,int]=None, start_ssdp_advertise=True):
        if local_addr is None:
            if_ips,_ = ifs.get_ifaces(config.master['network'])
            if not if_ips:
                raise RuntimeError(f'No interfaces found that are connected to the configured network {config.master["network"]}')
            local_addr = (if_ips[0], 0)
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
            self.ssdp_server = ssdp.Server(
                address=local_addr[0],
                host_ip_port=self.address[0],
                usn="humlab-b055-master::"+config.master['SSDP']['device_type'],
                device_type=config.master['SSDP']['device_type'])
            await self.ssdp_server.start()  # start listening to requests and respond with info about where we are
            await self.ssdp_server.send_notification()  # send one notification upon startup

    def is_serving(self):
        return self.server is not None

    async def stop_server(self):
        if self.ssdp_server is not None:
            await self.ssdp_server.stop()

        if self.server:
            self.server.close()
            await self.server.wait_closed()
        self.server = None

    async def _handle_client(self, reader: asyncio.streams.StreamReader, writer: asyncio.streams.StreamWriter):
        keepalive.set(writer.get_extra_info('socket'))

        me = structs.Client(writer)
        self._add_client(me)

        # request info about client
        await comms.typed_send(writer, message.Message.IDENTIFY)
        # and check if an eye tracker is connected
        await comms.typed_send(writer, message.Message.ET_STATUS_REQUEST)

        # process incoming messages
        type = None
        while type != message.Message.QUIT:
            try:
                type, msg = await comms.typed_receive(reader)
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

                    case message.Message.ET_STATUS_INFORM:
                        if not me.eye_tracker:
                            me.eye_tracker = eye_tracker.EyeTracker()
                        if msg['status']==eye_tracker.Status.Not_connected:
                            # eye tracker lost, clear properties
                            me.eye_tracker = eye_tracker.EyeTracker()   # NB: sets online to False
                        elif msg['status']==eye_tracker.Status.Connected:
                            me.eye_tracker.online = True
                            # ask for info about eye tracker
                            await comms.typed_send(writer, message.Message.ET_ATTR_REQUEST, '*')
                        # if timestamped, store as event
                        if 'timestamp' in msg:
                            self.client_et_events[me.id].append(msg)
                    case message.Message.ET_EVENT:
                        if not me.eye_tracker:
                            continue
                        # if timestamped, store as event
                        if 'timestamp' in msg:
                            self.client_et_events[me.id].append(msg)
                    case message.Message.ET_ATTR_UPDATE:
                        if not me.eye_tracker:
                            continue
                        # update attributes if any attached to message
                        if 'attributes' in msg:
                            eye_tracker.update_attributes(me.eye_tracker, msg['attributes'])
                        # if timestamped, store as event
                        if 'timestamp' in msg:
                            self.client_et_events[me.id].append(msg)


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
            await comms.typed_send(self.clients[c].writer, type, message)

    async def run_task(self,
                       type: task.Type,
                       payload: str,
                       known_clients: List[int] | str,
                       payload_type='text',
                       cwd: str=None,
                       env: dict=None,
                       interactive=False):
        # clients has a special value '*' which means all clients
        if known_clients=='*':
            known_clients = [c for c in self.known_clients]

        # handle payload
        match payload_type:
            case 'text':
                pass    # nothing to do, payload already in payload variable
            case 'file':
                async with aiofile.async_open(payload, 'rt') as afp:
                    payload = await afp.read()

        # make task group
        task_group, launch_group = task.create_group(type, payload, known_clients, cwd=cwd, env=env, interactive=interactive)
        self.task_groups[task_group.id] = task_group

        # start tasks
        coros = []
        for c in task_group.task_refs:
            mytask = task_group.task_refs[c]
            # add to client task list
            if self.known_clients[c].client:
                self.known_clients[c].client.tasks[mytask.id] = mytask
            if not launch_group:
                # send
                coros.append(task.send(mytask, self.known_clients[c]))
        if launch_group:
            coros.append(task.send(task_group, self.known_clients))

        await asyncio.gather(*coros)



def _SMB_get_shares(user, password, project=None):
    # figure out domain from user, default to configured
    domain = config.master["SMB"]["domain"]
    if '\\' in user['full_name']:
        dom, _ = user['full_name'].split('\\', maxsplit=1)
        if dom:
            domain = dom
    try:
        smb_hndl = smb.SMBHandler(config.master["SMB"]["server"], user['name'], domain, password)
    except (OSError, smb.SessionError) as exc:
        print(f'Error connecting as {domain}\{user["name"]} to {config.master["SMB"]["server"]}: {exc}')
        shares = []
    else:
        shares = smb_hndl.list_shares(matching=config.master["SMB"]["projects"]["format"], remove_trailing=config.master["SMB"]["projects"]["remove_trailing"], contains=project)

    return shares