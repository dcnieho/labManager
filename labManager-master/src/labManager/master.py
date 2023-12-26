import pkg_resources
import asyncio
import aiopath
import traceback
import sys
import threading
import json
import pathlib
import unicodedata
from typing import Callable
from dataclasses import dataclass, field

from labManager.common import async_thread, config, eye_tracker, message, structs, task
from labManager.common.network import admin_conn, comms, ifs, keepalive, smb, ssdp, toems


@dataclass
class ConnectedClient:
    reader          : asyncio.streams.StreamReader
    writer          : asyncio.streams.StreamWriter

    host            : str                   = None
    port            : int                   = None
    image_info      : dict[str,str]         = None
    eye_tracker     : eye_tracker           = None

    tasks           : dict[int, task.Task]  = field(default_factory=lambda: {})
    et_events       : list[dict]            = field(default_factory=lambda: [])
    mounted_shares  : dict[str,str]         = field(default_factory=lambda: {})

    def __post_init__(self):
        self.host,self.port = self.writer.get_extra_info('peername')

    def __repr__(self):
        return f'{self.name}@{self.host}:{self.port}'

class Master:
    def __init__(self):
        ### user interface
        # credentials
        self.username           : str                           = None
        self.password           : str                           = None
        # all projects user has access to and selected project
        self.projects           : dict[str, str]                = {}
        self.project            : str                           = None
        self.has_share_access   : bool                          = False

        # connections to servers
        self.admin              : admin_conn.Client             = None
        self.toems              : toems.Client                  = None

        # servers
        self.address            : str                           = None
        self.server             : str                           = None
        self.ssdp_server        : ssdp.Server                   = None

        self.clients            : dict[int, structs.Client]     = {}
        self.clients_lock       : threading.Lock                = threading.Lock()
        self.client_disconnected_hook: \
                                  Callable[[ConnectedClient, int], None] = None
        self._known_clients     : list[dict[str,str|list[str]]] = []

        self.task_groups        : dict[int, task.TaskGroup]     = {}
        self.task_state_change_hook: \
                                  Callable[[ConnectedClient, int, task.Task], None] = None

        self._file_action_id_provider = structs.CounterContext()

    def __del__(self):
        # cleanup: logout() takes care of all teardown
        self.logout()

    async def login(self, username: str, password: str):
        # clean up old session, if any
        await self._logout_async()

        # sanitize username and password, control characters mess with ldap
        username = "".join(ch for ch in username if unicodedata.category(ch)[0]!="C")
        password = "".join(ch for ch in password if unicodedata.category(ch)[0]!="C")

        # check user credentials, and list projects they have access to
        self.admin = admin_conn.Client(config.master['admin']['server'], config.master['admin']['port'])
        await self.admin.login(username, password)
        self.username, self.password = username, password

        # prep user's projects
        self.load_projects()

    def logout(self):
        if async_thread.loop and async_thread.loop.is_running:
            async_thread.run(self._logout_async())

    async def _logout_async(self):
        await self._unset_project_async()
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
        self.has_share_access = await smb.check_share(config.master["SMB"]["server"],
                                      self.admin.user['full_name'], self.password, project+config.master["SMB"]["projects"]["remove_trailing"],
                                      config.master["SMB"]["domain"], check_access_level=smb.AccessLevel.READ|smb.AccessLevel.WRITE|smb.AccessLevel.DELETE)

        # log into toems server
        await self.admin.prep_toems()
        self.toems = toems.Client(config.master['toems']['server'], config.master['toems']['port'], protocol='http')
        await self.toems.connect(self.username, self.password)
        self.project = project

    def unset_project(self):
        if async_thread.loop and async_thread.loop.is_running:
            async_thread.run(self._unset_project_async())

    async def _unset_project_async(self):
        await self.stop_server()
        self.toems = None
        self.project = None
        self.has_share_access = False
        if self.admin is not None:
            self.admin.unset_project()
        self.task_groups.clear()
        # NB: no need to clean up clients, stop_server() above will stop the connections, which cleans them up for us


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
                if self.clients[c].online:
                    try:
                        # break out of reading loop in client handler by sending it a quit message
                        self.clients[c].online.reader.feed_data(comms.prepare_transmission(message.Message.QUIT.value))
                    except Exception as exc:
                        # ok, try to shut down a bit more aggressively
                        try:
                            self.clients[c].online.writer.close()
                        except:
                            pass
            close_waiters = [self.clients[c].online.writer.wait_closed() for c in self.clients if self.clients[c].online]
        if close_waiters:
            await asyncio.wait(close_waiters)

        if self.server:
            self.server.close()
            await self.server.wait_closed()
        self.server = None

    async def _handle_client(self, reader: asyncio.streams.StreamReader, writer: asyncio.streams.StreamWriter):
        keepalive.set(writer.get_extra_info('socket'))

        me = ConnectedClient(reader, writer)
        id = None

        # request info about client
        await comms.typed_send(writer, message.Message.IDENTIFY)
        # and check if an eye tracker is connected
        await comms.typed_send(writer, message.Message.ET_STATUS_REQUEST)

        # process incoming messages
        while True:
            try:
                msg_type, msg = await comms.typed_receive(reader)
                if not msg_type:
                    # connection broken, close
                    break

                match msg_type:
                    case message.Message.QUIT:
                        break
                    case message.Message.IDENTIFY:
                        if 'image_info' in msg:
                            me.image_info = msg['image_info']
                        id = self._client_connected(me, msg['name'], msg['MACs'])

                        # if wanted and available, tell client to mount project share as drive
                        if self.has_share_access and config.master['SMB']['mount_share_on_client']:
                            # check if we're allowed to issue mount command to this client
                            if (config.master['SMB']['mount_only_known_clients'] and self.clients[id].known) or not config.master['SMB']['mount_only_known_clients']:
                                domain, user = smb.get_domain_username(self.admin.user['full_name'], config.master["SMB"]["domain"])
                                request = {
                                    'drive': config.master['SMB']['mount_drive_letter'],
                                    'share_path': f'\\\\{config.master["SMB"]["server"]}\{self.project}{config.master["SMB"]["projects"]["remove_trailing"]}',
                                    'user': f'{domain}\{user}',
                                    'password': self.password
                                    }
                                await comms.typed_send(writer, message.Message.SHARE_MOUNT, request)
                                me.mounted_shares[request['drive']] = request['share_path']

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
                            me.et_events.append(msg)
                    case message.Message.ET_EVENT:
                        if not me.eye_tracker:
                            continue
                        # if timestamped, store as event
                        if 'timestamp' in msg:
                            me.et_events.append(msg)
                    case message.Message.ET_ATTR_UPDATE:
                        if not me.eye_tracker or not msg:
                            continue
                        # update attributes if any attached to message
                        if 'attributes' in msg and msg['attributes']:
                            eye_tracker.update_attributes(me.eye_tracker, msg['attributes'])
                        # if timestamped, store as event
                        if 'timestamp' in msg:
                            me.et_events.append(msg)

                    case message.Message.TASK_OUTPUT:
                        mytask = me.tasks[msg['task_id']]
                        # NB: ignore msg['stream_type'] and just concat all to one text buffer
                        mytask.output += msg['output']
                    case message.Message.TASK_UPDATE:
                        mytask = me.tasks[msg['task_id']]
                        status_change = mytask.status!=msg['status']
                        mytask.status = msg['status']
                        if 'return_code' in msg:
                            mytask.return_code = msg['return_code']
                        if status_change and self.task_state_change_hook:
                            self.task_state_change_hook(me, id, mytask)

                    case _:
                        print(f'got unhandled type {msg_type.value}, message: {msg}')

            except Exception as exc:
                tb_lines = traceback.format_exception(exc)
                print("".join(tb_lines))
                continue

        await self.unmount_client_shares(me)
        writer.close()
        me.writer = None

        # remove online client instance
        self._client_disconnected(me, id)


    def load_known_clients(self, known_clients: list[dict[str,str|list[str]]] = None):
        if not known_clients:
            known_clients = config.master['clients']
        self._known_clients = known_clients

        with self.clients_lock:
            # first remove clients that are not online and not in the new known_clients
            names = [client['name'] for client in self._known_clients]
            self.clients = {c:v for c,v in self.clients if v.name in names}

            # add clients that we don't know yet (assume unique names)
            names = [self.clients[c].name for c in self.clients]
            for client in self._known_clients:
                if client['name'] in names:
                    continue
                client = structs.Client(client['name'], client['MAC'], known=True)
                self.clients[client.id] = client

    def _client_connected(self, client: ConnectedClient, name, MACs):
        with self.clients_lock:
            for c in self.clients:
                if self.clients[c].name != name:
                    continue
                for m in self.clients[c].MACs:
                    if m in MACs:
                        # known client, registrer online instance to it
                        self.clients[c].online = client
                        return self.clients[c].id # we're done

            # client not known, add
            c = structs.Client(name, MACs, online=client)
            self.clients[c.id] = c
            return c.id

    def _client_disconnected(self, client: ConnectedClient, id: int):
        if self.client_disconnected_hook:
            self.client_disconnected_hook(client, id)
        if id in self.clients:
            self.clients[id].online = None
            # if not a known client, remove from self.clients
            if not self.clients[id].known:
                with self.clients_lock:
                    del self.clients[id]


    async def broadcast(self, type: message.Message, msg: str=''):
        with self.clients_lock:
            coros = [comms.typed_send(self.clients[c].online.writer, type, msg) for c in self.clients if self.clients[c].online]
        await asyncio.gather(*coros)

    async def unmount_client_shares(self, client: ConnectedClient):
        if not client.writer or client.writer.is_closing():
            return
        coros = []
        for drive in client.mounted_shares.keys():
            coros.append(comms.typed_send(client.writer, message.Message.SHARE_UNMOUNT, {'drive': drive}))
        await asyncio.gather(*coros)

    async def run_task(self,
                       type: task.Type,
                       payload: str,
                       clients: list[int] | str,
                       payload_type='text',
                       cwd: str=None,
                       env: dict=None,
                       interactive=False,
                       python_unbuf=False):
        # clients has a special value '*' which means all clients
        if clients=='*':
            with self.clients_lock:
                clients = [c for c in self.clients]
        if not clients:
            # nothing to do
            return

        # handle payload
        match payload_type:
            case 'text':
                pass    # nothing to do, payload already in payload variable
            case 'file':
                payload = await aiopath.AsyncPath(payload).read_text()

        # make task group
        task_group, launch_group = task.create_group(type, payload, clients, cwd=cwd, env=env, interactive=interactive, python_unbuf=python_unbuf)
        self.task_groups[task_group.id] = task_group

        # start tasks
        coros = []
        for c in task_group.task_refs:  # NB: index is client ID
            mytask = task_group.task_refs[c]
            # add to client task list
            if self.clients[c].online:
                self.clients[c].online.tasks[mytask.id] = mytask
            if not launch_group:
                # send
                coros.append(task.send(mytask, self.clients[c]))
        if launch_group:
            coros.append(task.send(task_group, self.clients))

        await asyncio.gather(*coros)

        # return TaskGroup.id and [Task.id, ...] for all constituent tasks
        return task_group.id, [task_group.task_refs[c].id for c in task_group.task_refs]


    async def get_client_drives(self, client: structs.Client):
        await comms.typed_send(client.online.writer, message.Message.FILE_GET_DRIVES)

    async def get_client_file_listing(self, client: structs.Client, path: str|pathlib.Path):
        await comms.typed_send(client.online.writer, message.Message.FILE_GET_LISTING,
                               {'path': path})

    async def get_client_remote_shares(self, client: structs.Client, net_name: str, user: str = 'Guest', password: str = '', domain: str = '', access_level: smb.AccessLevel = smb.AccessLevel.READ):
        # list shares on specified target machine that are accessible from this client
        await comms.typed_send(client.online.writer, message.Message.FILE_GET_SHARES,
                               {'net_name': net_name,
                                'user': user,
                                'password': password,
                                'domain': domain,
                                'access_level': access_level})

    async def _make_client_file_folder(self, client: structs.Client, path: str|pathlib.Path, is_dir: bool):
        id = self._file_action_id_provider.get_next()
        await comms.typed_send(client.online.writer, message.Message.FILE_MAKE,
                               {'path': path,
                                'is_dir': is_dir,
                                'action_id': id})
        return id
    async def make_client_file  (self, client: structs.Client, path: str|pathlib.Path):
        return await self._make_client_file_folder(client, path, False)
    async def make_client_folder(self, client: structs.Client, path: str|pathlib.Path):
        return await self._make_client_file_folder(client, path, True)

    async def rename_client_file_folder(self, client: structs.Client, old_path: str|pathlib.Path, new_path: str|pathlib.Path):
        id = self._file_action_id_provider.get_next()
        await comms.typed_send(client.online.writer, message.Message.FILE_RENAME,
                               {'old_path': old_path,
                                'new_path': new_path,
                                'action_id': id})
        return id

    async def _copy_move_client_file_folder(self, client: structs.Client, source_path: str|pathlib.Path, dest_path: str|pathlib.Path, is_move: bool):
        id = self._file_action_id_provider.get_next()
        await comms.typed_send(client.online.writer, message.Message.FILE_COPY_MOVE,
                               {'source_path': source_path,
                                'dest_path': dest_path,
                                'is_move': is_move,
                                'action_id': id})
        return id
    async def copy_client_file_folder(self, client: structs.Client, source_path: str|pathlib.Path, dest_path: str|pathlib.Path):
        return await self._copy_move_client_file_folder(client, source_path, dest_path, False)
    async def move_client_file_folder(self, client: structs.Client, source_path: str|pathlib.Path, dest_path: str|pathlib.Path):
        return await self._copy_move_client_file_folder(client, source_path, dest_path, True)

    async def delete_client_file_folder(self, client: structs.Client, path: str|pathlib.Path):
        id = self._file_action_id_provider.get_next()
        await comms.typed_send(client.online.writer, message.Message.FILE_DELETE,
                               {'path': path,
                                'action_id': id})
        return id


    async def get_computers(self):
        return await self.toems.computer_get(filter_list=[c['name'] for c in config.master['clients']])

    async def get_images(self):
        return await self.toems.image_get(project=self.project, project_format=config.master['toems']['images']['format'], name_mapping=config.master['base_image_name_table'] if 'base_image_name_table' in config.master else None)

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
            resp = await self.admin.image_set_script(image_id, config.master['toems']['image_info_script'], script, priority=1, run_when=3)
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
    async_thread.wait(gui.master.stop_server())
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