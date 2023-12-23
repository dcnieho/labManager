import pkg_resources
import asyncio
import aiofile
import traceback
import sys
import threading
import json
from typing import Callable

from labManager.common import async_thread, config, eye_tracker, message, structs, task
from labManager.common.network import admin_conn, comms, ifs, keepalive, smb, ssdp, toems

class Master:
    def __init__(self):
        ### user interface
        # credentials
        self.username: str              = None
        self.password: str              = None
        # all projects user has access to and selected project
        self.projects: dict[str, str]   = {}
        self.project:  str              = None
        self.has_share_access           = False

        # connections to servers
        self.admin:    admin_conn.Client= None
        self.toems:    toems.Client     = None

        # servers
        self.address:  str              = None
        self.server:   str              = None
        self.ssdp_server: ssdp.Server   = None

        self.clients: dict[int, structs.Client] = {}
        self.clients_lock = threading.Lock()
        self.known_clients: dict[int, structs.KnownClient] = {}
        self.known_clients_lock = threading.Lock()
        self.client_et_events: dict[int, list[dict]] = {}
        self.remove_client_hook: Callable = None

        self.task_groups: dict[int, task.TaskGroup] = {}
        self.task_state_change_hook: Callable = None

    def __del__(self):
        # cleanup: logout() takes care of all teardown
        self.logout()

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

        # ensure possible previous project is unloaded
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
        if async_thread.loop and async_thread.loop.is_running:
            async_thread.run(self.stop_server())
        self.toems = None
        self.project = None
        self.has_share_access = False
        self.unmount_client_shares()
        if self.admin is not None:
            self.admin.unset_project()
        self.task_groups.clear()
        self.client_et_events.clear()
        with self.clients_lock:
            self.clients.clear()


    async def get_computers(self):
        return await self.toems.computer_get(filter_list=[c['name'] for c in config.master['clients']])

    async def get_images(self):
        return await self.toems.image_get(project=self.project, project_format=config.master['toems']['images']['format'])

    async def get_image_size(self, name_or_id: int|str):
        return await self.toems.image_get_server_size(name_or_id)

    async def get_image_info(self, name_or_id: int|str):
        image = (await self.toems.image_get(name_or_id))

        # get timestamp last time image was updated
        im_logs = await self.toems.image_get_audit_log(image['Id'])
        for l in im_logs:   # NB: logs are sorted newest-first
            if l['AuditType'] in ['Upload','OndUpload']:
                upload_info = json.loads(l['ObjectJson'])
                computer = await self.toems.computer_get(upload_info['ComputerId'])
                return {
                    'TimeStamp': l['DateTime'],
                    'SourceComputer': computer['Name']
                }
        return None     # no info found

    async def create_image(self, name: str, description: str|None = None):
        return await self.admin.create_image(name, description)

    async def update_image(self, name: str, updates):
        image_id = (await self.toems.image_get(name))['Id']
        return await self.admin.update_image(image_id, updates)

    async def delete_image(self, name: str):
        image_id = (await self.toems.image_get(name))['Id']
        return await self.admin.delete_image(image_id)

    async def deploy_image(self, image: str, part_of_project: bool, computers: list[str]):
        image_id = (await self.toems.image_get(image))['Id']

        # update image info script
        if 'image_info_script' in config.master['toems']:
            im_info = await self.get_image_info(image_id)
            info = {"name": image}
            if im_info is None:
                info['timestamp'] = None
                info['source_computer'] = None
            else:
                info['timestamp'] = im_info['TimeStamp']
                info['source_computer'] = im_info['SourceComputer']
            if part_of_project:
                info['project'] = self.project
            script = toems.make_info_script(info, config.master['toems']['image_info_script_partition'])
            resp = await self.admin.image_set_script(image_id, config.master['toems']['image_info_script'], script, priority=1, run_when=2)
            if not resp['Success']:
                raise RuntimeError(f"can't deploy: failed to set image info script ({resp['ErrorMessage']})")
        if 'pre_upload_script' in config.master['toems']:
            # if there is a pre-upload script, disable it
            resp = await self.admin.image_set_script(image_id, config.master['toems']['pre_upload_script'], '', priority=0, run_when=0)
            if not resp['Success']:
                raise RuntimeError(f"can't deploy: failed to disable image cleanup script ({resp['ErrorMessage']})")

        if isinstance(computers,str):
            computers = [computers]
        comps = await asyncio.gather(*[self.toems.computer_get(c) for c in computers])
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

        # handle scripts
        if 'pre_upload_script' in config.master['toems']:
            # if there is a pre-upload script, enable it
            resp = await self.admin.image_set_script(image_id, config.master['toems']['pre_upload_script'], '', priority=0, run_when=1)
            if not resp['Success']:
                raise RuntimeError(f"can't upload: failed to set image cleanup script ({resp['ErrorMessage']})")
        if 'image_info_script' in config.master['toems']:
            # if there is a post-deploy script, disable it
            resp = await self.admin.image_set_script(image_id, config.master['toems']['image_info_script'], '', priority=1, run_when=0)
            if not resp['Success']:
                raise RuntimeError(f"can't upload: failed to unset image info script ({resp['ErrorMessage']})")

        resp = await self.admin.update_image(image_id, {"Protected": False})
        if resp['Protected']:   # check it worked
            raise RuntimeError(f"can't upload: failed to unprotect image ({resp['ErrorMessage']})")

        resp = await self.toems.computer_upload(comp_id, image_id)
        if not resp['Success']:
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
            item['Status'] = r['Status']
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
        with self.clients_lock:
            self.clients[client.id] = client
            self.client_et_events[client.id] = []

    def _remove_client(self, client: structs.Client):
        if self.remove_client_hook:
            self.remove_client_hook(client)
        self._remove_known_client(client)
        self.unmount_client_shares(client.writer)
        with self.clients_lock:
            if client.id in self.clients:
                del self.clients[client.id]
            if client.id in self.client_et_events:
                del self.client_et_events[client.id]

    def load_known_clients(self, known_clients: list[dict[str,str|list[str]]] = None):
        if not known_clients:
            known_clients = config.master['clients']
        with self.known_clients_lock:
            for client in known_clients:
                kc = structs.KnownClient(client['name'], client['MAC'], configured=True)
                self.known_clients[kc.id] = kc

    def _find_or_add_known_client(self, client: structs.Client):
        with self.known_clients_lock:
            for id in self.known_clients:
                for m in self.known_clients[id].MAC:
                    if m in client.MACs:
                        client.known_client = self.known_clients[id]
                        self.known_clients[id].client = client
                        return True # known client

            # client not known, add
            kc = structs.KnownClient(client.name, client.MACs, client=client)
            self.known_clients[kc.id] = kc
            client.known_client = self.known_clients[kc.id]
            return False    # unknown client

    def _remove_known_client(self, client: structs.Client):
        if client.known_client:
            client.known_client.client = None
            # if not a preconfigured known client, remove from list so that if this one reconnects, its not falsely listed as known
            if not client.known_client.configured:
                with self.known_clients_lock:
                    if client.known_client.id in self.known_clients:
                        del self.known_clients[client.known_client.id]
        client.known_client = None

    def unmount_client_shares(self, writer=None):
        if not async_thread.loop or not async_thread.loop.is_running:
            return
        if config.master['SMB']['mount_share_on_client']:
            request = {'drive': config.master['SMB']['mount_drive_letter']}
            if writer is not None:
                coro = comms.typed_send(writer, message.Message.SHARE_UNMOUNT, request)
            else:
                coro = self.broadcast(message.Message.SHARE_UNMOUNT, request)
            async_thread.run(coro)

    async def start_server(self, local_addr: tuple[str,int]=None, start_ssdp_advertise=True):
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

        with self.clients_lock:
            for c in self.clients:
                try:
                    self.clients[c].writer.close()
                except:
                    pass
            close_waiters = [asyncio.create_task(self.clients[c].writer.wait_closed()) for c in self.clients]
        if close_waiters:
            await asyncio.wait(close_waiters)

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
                        if 'image_info' in msg:
                            me.image_info = msg['image_info']
                        self._find_or_add_known_client(me)

                        # if wanted, tell client to mount drive
                        if config.master['SMB']['mount_share_on_client']:
                            # check if we're allowed to issue mount command to this client
                            if (config.master['SMB']['mount_only_known_clients'] and me.known_client.configured) or not config.master['SMB']['mount_only_known_clients']:
                                domain, user = _get_SMB_domain_username(self.admin.user['full_name'])
                                request = {
                                    'drive': config.master['SMB']['mount_drive_letter'],
                                    'share_path': f'\\\\{config.master["SMB"]["server"]}\{self.project}{config.master["SMB"]["projects"]["remove_trailing"]}',
                                    'user': f'{domain}\{user}',
                                    'password': self.password
                                    }
                                await comms.typed_send(writer, message.Message.SHARE_MOUNT, request)

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
                        if not me.eye_tracker or not msg:
                            continue
                        # update attributes if any attached to message
                        if 'attributes' in msg and msg['attributes']:
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
                        status_change = mytask.status!=msg['status']
                        mytask.status = msg['status']
                        if 'return_code' in msg:
                            mytask.return_code = msg['return_code']
                        if status_change and self.task_state_change_hook:
                            self.task_state_change_hook(me, mytask)

                    case _:
                        print(f'got unhandled type {type.value}, message: {msg}')

            except Exception as exc:
                tb_lines = traceback.format_exception(exc)
                print("".join(tb_lines))
                continue

        writer.close()
        me.writer = None

        # remove from client list
        self._remove_client(me)

    async def broadcast(self, type: message.Message, message: str=''):
        with self.clients_lock:
            coros = [comms.typed_send(self.clients[c].writer, type, message) for c in self.clients]
        await asyncio.gather(*coros)

    async def run_task(self,
                       type: task.Type,
                       payload: str,
                       known_clients: list[int] | str,
                       payload_type='text',
                       cwd: str=None,
                       env: dict=None,
                       interactive=False,
                       python_unbuf=False):
        # clients has a special value '*' which means all clients
        if known_clients=='*':
            with self.known_clients_lock:
                known_clients = [c for c in self.known_clients]
        if not known_clients:
            # nothing to do
            return

        # handle payload
        match payload_type:
            case 'text':
                pass    # nothing to do, payload already in payload variable
            case 'file':
                async with aiofile.async_open(payload, 'rt') as afp:
                    payload = await afp.read()

        # make task group
        task_group, launch_group = task.create_group(type, payload, known_clients, cwd=cwd, env=env, interactive=interactive, python_unbuf=python_unbuf)
        self.task_groups[task_group.id] = task_group

        # start tasks
        coros = []
        for c in task_group.task_refs:  # NB: index is client ID
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

        # return TaskGroup.id and [Task.id, ...] for all constituent tasks
        return task_group.id, [task_group.task_refs[c].id for c in task_group.task_refs]


def _check_has_GUI():
    if 'imgui-bundle' not in {pkg.key for pkg in pkg_resources.working_set}:
        raise RuntimeError('You must install labManager-master with the [GUI] extra if you wish to use the GUI. Required dependencies for the GUI not available...')

# run GUI master - returns when GUI is closed
def run_GUI():
    _check_has_GUI()
    from labManager.GUI import master as master_GUI
    if getattr(sys, "frozen", False) and "nohide" not in sys.argv:
        import ctypes
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)

    gui = master_GUI.MainGUI()
    gui.run()
# GUI (and master in general) requires some setup, call these functions
def set_up():
    async_thread.setup()
def clean_up():
    async_thread.cleanup()

async def cmd_login_flow(master: Master, username: str = None, password: str = None, project: str = None):
    if not username:
        username = input(f'Username: ')
    if not password:
        from getpass import getpass
        password = getpass(f'Password: ')
    await master.login(username, password)

    if not project:
        print('You have access to the following projects, which would you like to use?')
        for p,pn in master.projects.items():
            if pn==p:
                print(f'  {p}')
            else:
                print(f'  {p} ({pn})')
        project = input(f'Project: ')
    await master.set_project(project)

def _SMB_get_shares(user, password, project=None):
    domain, user = _get_SMB_domain_username(user['full_name'])
    try:
        smb_hndl = smb.SMBHandler(config.master["SMB"]["server"], user, domain, password)
    except (OSError, smb.SessionError) as exc:
        print(f'Error connecting as {domain}\{user} to {config.master["SMB"]["server"]}: {exc}')
        shares = []
    else:
        shares = smb_hndl.list_shares(matching=config.master["SMB"]["projects"]["format"], remove_trailing=config.master["SMB"]["projects"]["remove_trailing"], contains=project)

    return shares

def _get_SMB_domain_username(user):
    # figure out domain from user, default to configured
    domain = config.master["SMB"]["domain"]
    if '\\' in user:
        dom, user = user.split('\\', maxsplit=1)
        if dom:
            domain = dom
    return domain, user