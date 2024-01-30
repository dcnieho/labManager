import pkg_resources
import asyncio
import aiopath
import traceback
import sys
import threading
import json
import pathlib
import platform
import unicodedata
import time
from typing import Any, Callable

from labManager.common import async_thread, config, counter, eye_tracker, file_actions, message, structs, task
from labManager.common.network import admin_conn, comms, ifs, keepalive, mdns, ssdp, toems
from labManager.common.network import utils as net_utils

__version__ = '1.0.5'



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

        self._waiters           : set[structs.Waiter]           = set()

        # connections to servers
        self.admin              : admin_conn.Client             = None
        self.toems              : toems.Client                  = None

        # servers
        self.address            : str                           = None
        self._server            : asyncio.Server                = None
        self._ssdp_server       : ssdp.Server                   = None
        self._mnds_announcer    : mdns.Announcer                = None
        self._mnds_announcer_task: asyncio.Task                 = None

        # clients
        self.clients            : dict[int, structs.Client]     = {}
        self.clients_lock       : threading.Lock                = threading.Lock()
        self._known_clients     : list[dict[str,str|list[str]]] = []

        # tasks
        self.task_groups        : dict[int, task.TaskGroup]     = {}

        # file actions
        self._file_action_id_provider = counter.CounterContext()

        # hooks
        self.login_state_change_hooks: \
            list[Callable[[structs.Status, Exception|None], None]]= []
        self.project_selection_state_change_hooks: \
            list[Callable[[structs.Status, Exception|None], None]]= []
        self.server_state_change_hooks: \
            list[Callable[[structs.Status], None]]= []
        self.client_disconnected_hooks: \
            list[Callable[[structs.ConnectedClient, int], None]]= []
        self.task_state_change_hooks: \
            list[Callable[[structs.ConnectedClient, int, task.Task], None]] = []

    def __del__(self):
        # if there are any registered waiters, cancel them
        for w in self._waiters:
            w.fut.cancel()

        # cleanup: logout() takes care of all teardown
        self.logout()

    async def login(self, username: str, password: str):
        # clean up old session, if any
        await self._logout_async()
        self._call_hooks(self.login_state_change_hooks, structs.Status.Running)

        try:
            # check preconditions
            if not 'admin' in config.master:
                raise LookupError('You cannot login without the admin config item being set in your configuration yaml file')
            if not 'toems' in config.master:    # technically we need this only when selecting a project, but may as well error now
                raise LookupError('You cannot login without the toems config item being set in your configuration yaml file')

            # sanitize username and password, control characters mess with ldap
            username = "".join(ch for ch in username if unicodedata.category(ch)[0]!="C")
            password = "".join(ch for ch in password if unicodedata.category(ch)[0]!="C")

            # check user credentials, and list projects they have access to
            admin = admin_conn.Client(config.master['admin']['server'], config.master['admin']['port'])
            await admin.login(username, password)
            self.username, self.password = username, password
            self.admin = admin

            # prep user's projects
            self.load_projects()
        except Exception as exc:
            self._call_hooks(self.login_state_change_hooks, structs.Status.Errored, exc)
            raise
        else:
            self._call_hooks(self.login_state_change_hooks, structs.Status.Finished)

    def logout(self):
        if async_thread.loop and async_thread.loop.is_running:
            async_thread.run(self._logout_async())

    async def _logout_async(self):
        await self._unset_project_async()
        self.username, self.password = None, None
        self.projects = {}
        self.admin = None
        self._call_hooks(self.login_state_change_hooks, structs.Status.Pending)

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
        try:
            self._call_hooks(self.project_selection_state_change_hooks, structs.Status.Running)
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

            # check preconditions
            if not 'toems' in config.master:    # technically we need this only when selecting a project, but may as well error now
                raise LookupError('You cannot login without the toems config item being set in your configuration yaml file')

            # ensure possible previous project is unloaded
            self.toems = None

            # set new project
            self.admin.set_project(project)

            # log into toems server
            await self.admin.prep_toems()
            toems_ = toems.Client(config.master['toems']['server'], config.master['toems']['port'], protocol='http')
            await toems_.connect(self.username, self.password)
            self.project = project
            self.toems = toems_

            # check share access
            domain, user = net_utils.get_domain_username(self.admin.user['full_name'], config.master["SMB"]["domain"])
            self.has_share_access = file_actions.check_share(config.master["SMB"]["server"], project+config.master["SMB"]["projects"]["remove_trailing"],
                                                             user, self.password, domain)


        except Exception as exc:
            self._call_hooks(self.project_selection_state_change_hooks, structs.Status.Errored, exc)
            raise
        else:
            self._call_hooks(self.project_selection_state_change_hooks, structs.Status.Finished)
            for w in self._waiters:
                if w.waiter_type==structs.WaiterType.Login_Project_Select and not w.fut.done():
                    w.fut.set_result(None)


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
        self._call_hooks(self.project_selection_state_change_hooks, structs.Status.Pending)
        # NB: no need to clean up clients, stop_server() above will stop the connections, which cleans them up for us


    async def start_server(self, local_addr: tuple[str,int]=None, announcer: str='mdns'):
        if self.is_serving():
            return

        if local_addr is None:
            if_ips,_ = ifs.get_ifaces(config.master['network'])
            if not if_ips:
                raise RuntimeError(f'No interfaces found that are connected to the configured network {config.master["network"]}')
            local_addr = (if_ips[0], 0)
        self._server = await asyncio.start_server(self._handle_client, *local_addr)

        addr = [sock.getsockname() for sock in self._server.sockets]
        if len(addr[0])!=2:
            addr[0], addr[1] = addr[1], addr[0]
        self.address = addr

        # should already have started serving in asyncio.start_server, but to be safe and sure:
        await self._server.start_serving()

        # start SSDP server if wanted
        if announcer.casefold()=='ssdp':
            # start SSDP server to advertise this server
            self._ssdp_server = ssdp.Server(
                address=local_addr[0],
                host_ip_port=self.address[0],
                usn="humlab-b055-master::"+config.master['SSDP']['device_type'],
                device_type=config.master['SSDP']['device_type'])
            await self._ssdp_server.start()  # start listening to requests and respond with info about where we are
            await self._ssdp_server.send_notification()  # send one notification upon startup
        elif announcer.casefold()=='mdns':
            self._mnds_announcer = mdns.Announcer(
                ip_network=config.master['network'],
                service=config.master['MDNS']['service'],
                name=f'{platform.node()}_{self.address[0][0].split(".")[-1]}_{self.address[0][1]}',
                address=self.address[0])
            self._mnds_announcer_task = asyncio.create_task(self._mnds_announcer.run())

        # done, notify we're running
        self._call_hooks(self.server_state_change_hooks, structs.Status.Running)
        for w in self._waiters:
            if w.waiter_type==structs.WaiterType.Server_Started and not w.fut.done():
                w.fut.set_result(None)

    def is_serving(self):
        return self._server is not None and self._server.is_serving()

    async def stop_server(self):
        if self._ssdp_server is not None:
            await self._ssdp_server.stop()
        if self._mnds_announcer_task and not self._mnds_announcer_task.done():
            self._mnds_announcer_task.cancel()
            await asyncio.wait([self._mnds_announcer_task], timeout=1)

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
            close_waiters = [self.clients[c].online.writer.wait_closed() for c in self.clients if self.clients[c].online and self.clients[c].online.writer]
        if close_waiters:
            _, not_finished = await asyncio.wait(close_waiters, timeout=1)
            # it seems sometimes no progress is made, not sure why
            # closing down a bit more roughly seems to kick things into shape if that happens
            if not_finished:
                with self.clients_lock:
                    for c in self.clients:
                        try:
                            self.clients[c].online.writer.close()
                        except:
                            pass
                await asyncio.wait(not_finished)

        self.task_groups.clear()

        if self._server:
            self._server.close()
            await self._server.wait_closed()
        self._server = None

        # done, notify we're not running
        self._call_hooks(self.server_state_change_hooks, structs.Status.Pending)

    def add_waiter(self, waiter_type : str|structs.WaiterType, parameter: str|pathlib.Path|int|None=None, parameter2: int|None = None):
        waiter_type = structs.WaiterType.get(waiter_type)

        # check parameter is valid
        match waiter_type:
            case structs.WaiterType.Login_Project_Select | \
                 structs.WaiterType.Server_Started | \
                 structs.WaiterType.Client_Connect_Any | \
                 structs.WaiterType.Client_Disconnect_Any | \
                 structs.WaiterType.Task_Any:
                pass    # no parameters
            case structs.WaiterType.Client_Connect_Name:
                # str: wait until client with this specific name is connected
                assert isinstance(parameter, str),\
                    f'When creating a {waiter_type.value} waiter, parameter should be an str (name of client to wait for)'
            case structs.WaiterType.Client_Disconnect_Name:
                # str: wait until client with this specific name has disconnected
                assert isinstance(parameter, str),\
                    f'When creating a {waiter_type.value} waiter, parameter should be an str (name of client to wait for)'
            case structs.WaiterType.Client_Connected_Nr:
                # int: wait until a specific number of clients is connected
                assert isinstance(parameter, int),\
                    f'When creating a {waiter_type.value} waiter, parameter should be an int (number of clients to wait for)'
            case structs.WaiterType.Task:
                assert isinstance(parameter, int),\
                    f'When creating a {waiter_type.value} waiter, parameter should be an int (task id)'
            case structs.WaiterType.Task_Group:
                assert isinstance(parameter, int),\
                    f'When creating a {waiter_type.value} waiter, parameter should be an int (task group id)'
            case structs.WaiterType.File_Listing:
                assert isinstance(parameter, str) or isinstance(parameter, pathlib.Path),\
                    f'When creating a {waiter_type.value} waiter, parameter should be an str or a pathlib.Path (listing path)'
                assert isinstance(parameter2, int),\
                    f'When creating a {waiter_type.value} waiter, parameter2 should be an int (client id)'
            case structs.WaiterType.File_Action:
                assert isinstance(parameter, int),\
                    f'When creating a {waiter_type.value} waiter, parameter should be an int (file action id)'

        # its valid, create our waiter
        if not async_thread.loop:
            raise RuntimeError('Error making waiter because event loop could not be retrieved. Make sure you call labManager.common.async_thread.setup()')
        waiter = structs.Waiter(waiter_type, parameter, parameter2, async_thread.loop.create_future())

        # register future and add our cleanup function
        self._waiters.add(waiter)
        waiter.fut.add_done_callback(lambda _: self._waiters.discard(waiter))

        # some extra set up or checks
        match waiter_type:
            case structs.WaiterType.Client_Connect_Any | \
                 structs.WaiterType.Client_Disconnect_Any | \
                 structs.WaiterType.Task_Any:
                pass # nothing to do
            case structs.WaiterType.Login_Project_Select:
                if self.username and self.password and self.project:
                    # already logged in and project selected, set future done
                    waiter.fut.set_result(None)
            case structs.WaiterType.Server_Started:
                if self.is_serving():
                    # server already started, set future done
                    waiter.fut.set_result(None)
            case structs.WaiterType.Client_Connect_Name:
                if parameter in [self.clients[c].name for c in self.clients if self.clients[c].online]:
                    # client with this name is already connected, set future done
                    waiter.fut.set_result(None)
            case structs.WaiterType.Client_Disconnect_Name:
                if parameter not in [self.clients[c].name for c in self.clients if self.clients[c].online]:
                    # client with this name is already connected, set future done
                    waiter.fut.set_result(None)
            case structs.WaiterType.Client_Connected_Nr:
                if len([c for c in self.clients if self.clients[c].online])==parameter:
                    # condition already met, set future done
                    waiter.fut.set_result(None)
            case structs.WaiterType.Task:
                # see if task exists, and if so if its already done
                # find the task somewhere in all the task groups
                tsk = None
                for tg in reversed(self.task_groups):   # check newest first, more likely to be found there
                    for c in self.task_groups[tg].tasks:
                        if self.task_groups[tg].tasks[c].id==parameter:
                            # found
                            tsk = self.task_groups[tg].tasks[c]
                            break
                    if tsk:
                        break
                # now register waiter for task if task was found
                if not tsk:
                    waiter.fut.set_exception(ValueError(f'task with id {parameter} does not exist'))
                elif tsk.is_done():
                    waiter.fut.set_result(None)
                else:
                    # waiting for these is accomplished by means of a callback that fires when their status changes
                    tsk.add_listener(lambda t: waiter.fut.set_result(None) if t.is_done() and not waiter.fut.done() else None)
            case structs.WaiterType.Task_Group:
                # see if task group exists, and if so if its already done
                if parameter not in self.task_groups:
                    waiter.fut.set_exception(ValueError(f'task group with id {parameter} does not exist'))
                elif self.task_groups[parameter].is_done():
                    waiter.fut.set_result(None)
                else:
                    # waiting for these is accomplished by means of a callback that fires when their status changes
                    self.task_groups[parameter].add_listener(lambda tg: waiter.fut.set_result(None) if tg.is_done() and not waiter.fut.done() else None)
            case structs.WaiterType.File_Listing:
                # nothing to do. special case. A file listing with this key is likely already present
                # but we wait for a new one to come in. It is advised to make the waiter before
                # launching the listing request, just to be very safe about avoid a race condition
                pass
            case structs.WaiterType.File_Action:
                # see if file action, and if so if its already done
                # find file action
                for c in self.clients:
                    if self.clients[c].online and parameter in self.clients[c].online.file_actions:
                        # found
                        if self.clients[c].online.file_actions[parameter]['status'] in [structs.Status.Finished, structs.Status.Errored]:
                            # action is already finished
                            waiter.fut.set_result(None)
                        break

        return waiter.fut

    async def _handle_client(self, reader: asyncio.streams.StreamReader, writer: asyncio.streams.StreamWriter):
        keepalive.set(writer.get_extra_info('socket'))

        me = structs.ConnectedClient(reader, writer)
        client_id = None

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
                        client_id = self._client_connected(me, msg['name'], msg['MACs'])

                        # if available, tell client to mount project share as drive
                        if self.has_share_access:
                            await self.client_mount_project_share(me, client_id)

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
                        mytask.status = msg['status']
                        if 'return_code' in msg:
                            mytask.return_code = msg['return_code']
                        # call hooks, if any
                        self._call_hooks(self.task_state_change_hooks, me, client_id, mytask)
                        if mytask.is_done():
                            for w in self._waiters:
                                if w.waiter_type==structs.WaiterType.Task_Any and not w.fut.done():
                                    w.fut.set_result(None)


                    case message.Message.FILE_LISTING:
                        path = str(msg.pop('path')) # should always be sent as a plain string instead of pathlib.Path by client, but lets be safe
                        msg['age'] = time.time()
                        me.file_listings[path] = msg
                        for w in self._waiters:
                            if w.waiter_type==structs.WaiterType.File_Listing and str(w.parameter)==path and w.parameter2==client_id:
                                # NB: no need for lock as callback is not called
                                # immediately, but call_soon()
                                if not w.fut.done():
                                    w.fut.set_result(None)
                    case message.Message.FILE_ACTION_STATUS:
                        action_id = msg.pop('action_id')
                        me.file_actions[action_id] = msg
                        # check if there are any waiters for this action, notify them
                        if msg['status'] in [structs.Status.Finished, structs.Status.Errored]:
                            for w in self._waiters:
                                if w.waiter_type==structs.WaiterType.File_Action and w.parameter==action_id:
                                    # NB: no need for lock as callback is not called
                                    # immediately, but call_soon()
                                    if not w.fut.done():
                                        w.fut.set_result(None)

                    case _:
                        print(f'got unhandled type {msg_type.value}, message: {msg}')

            except Exception as exc:
                tb_lines = traceback.format_exception(exc)
                print("".join(tb_lines))
                continue

        await self.client_unmount_shares(me)
        writer.close()
        me.writer = None

        # remove online client instance
        self._client_disconnected(me, client_id)


    def add_hook(self, which: str, fun: Callable):
        match which:
            case 'login_state_change':
                self.login_state_change_hooks.append(fun)
            case 'project_selection_state_change':
                self.project_selection_state_change_hooks.append(fun)
            case 'server_state_change':
                self.server_state_change_hooks.append(fun)
            case 'client_disconnected':
                self.client_disconnected_hooks.append(fun)
            case 'task_state_change':
                self.task_state_change_hooks.append(fun)
            case _:
                raise ValueError('add_hook: hook type "{which}" not understood')

    def _call_hooks(self, hook_list: list[Callable], *args):
        to_del = []
        for i,h in enumerate(hook_list):
            try:
                h(*args)
            except:
                to_del.append(i)
        # remove crashing hooks so they are not called again
        for i in to_del[::-1]:
            del hook_list[i]


    def load_known_clients(self, known_clients: list[dict[str,str|list[str]]] = None):
        if not known_clients:
            if 'clients' not in config.master:
                return  # nothing to load
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

    def _client_connected(self, client: structs.ConnectedClient, name, MACs):
        client_id = None
        with self.clients_lock:
            for c in self.clients:
                if client_id is not None:
                    break
                if self.clients[c].name != name:
                    continue
                for m in self.clients[c].MACs:
                    if m in MACs:
                        # known client, registrer online instance to it
                        self.clients[c].online = client
                        client_id = self.clients[c].id
                        break

            # client not known, add
            if client_id is None:
                c = structs.Client(name, list(MACs), online=client)
                client_id = c.id
                self.clients[client_id] = c
            num_clients = len([c for c in self.clients if self.clients[c].online])

        # fire any relevant waiters
        for w in self._waiters:
            finish_future = False
            if w.waiter_type==structs.WaiterType.Client_Connect_Any:
                # waiting for any client to connect
                finish_future = True
            elif w.waiter_type==structs.WaiterType.Client_Connect_Name and self.clients[client_id].name==w.parameter:
                # waiting for client with a specific name to connect
                finish_future = True
            elif w.waiter_type==structs.WaiterType.Client_Connected_Nr and num_clients==w.parameter:
                # waiting for a specific number of clients to be connected
                finish_future = True

            if finish_future and not w.fut.done():
                w.fut.set_result(None)
        return client_id

    def _client_disconnected(self, client: structs.ConnectedClient, client_id: int):
        # call hooks, if any
        self._call_hooks(self.client_disconnected_hooks, client, client_id)

        # clean up ConnectedClient
        with self.clients_lock:
            if client_id in self.clients:
                self.clients[client_id].online = None
                # if not a known client, remove from self.clients
                if not self.clients[client_id].known:
                    del self.clients[client_id]

            num_clients = len([c for c in self.clients if self.clients[c].online])

        # fire any relevant waiters
        for w in self._waiters:
            finish_future = False
            if w.waiter_type==structs.WaiterType.Client_Disconnect_Any:
                # waiting for any client to disconnect
                finish_future = True
            elif w.waiter_type==structs.WaiterType.Client_Disconnect_Name and (client_id not in self.clients or (self.clients[client_id].name==w.parameter and not self.clients[client_id].online)):
                # waiting for client with a specific name to disconnect
                finish_future = True
            elif w.waiter_type==structs.WaiterType.Client_Connected_Nr and num_clients==w.parameter:
                # waiting for a specific number of clients to be connected
                finish_future = True

            if finish_future and not w.fut.done():
                w.fut.set_result(None)


    async def broadcast(self, msg_type: str|message.Message, msg: str=''):
        msg_type = message.Message.get(msg_type)
        with self.clients_lock:
            coros = [comms.typed_send(self.clients[c].online.writer, msg_type, msg) for c in self.clients if self.clients[c].online]
        await asyncio.gather(*coros)

    async def client_mount_project_share(self, client: structs.ConnectedClient, client_id: int):
        if self.has_share_access and 'SMB' in config.master and config.master['SMB']['mount_share_on_client']:
            # check if we're allowed to issue mount command to this client
            if (config.master['SMB']['mount_only_known_clients'] and self.clients[client_id].known) or not config.master['SMB']['mount_only_known_clients']:
                domain, user = net_utils.get_domain_username(self.admin.user['full_name'], config.master["SMB"]["domain"])
                await self.client_mount_share(
                    client, drive=config.master['SMB']['mount_drive_letter'],
                    share_path=f'\\\\{config.master["SMB"]["server"]}\{self.project}{config.master["SMB"]["projects"]["remove_trailing"]}',
                    user=user, password=self.password, domain=domain
                )

    async def client_mount_share(self, client: structs.ConnectedClient, drive: str, share_path: str|pathlib.Path, user: str, password: str, domain: str = ''):
        request = {
            'drive': drive,
            'share_path': share_path,
            'user': f'{domain}\{user}' if domain else user,
            'password': password
        }
        await comms.typed_send(client.writer, message.Message.SHARE_MOUNT, request)
        client.mounted_shares[request['drive']] = request['share_path']

    async def client_unmount_shares(self, client: structs.ConnectedClient):
        if not client.writer or client.writer.is_closing():
            return
        coros = []
        for drive in client.mounted_shares.keys():
            coros.append(comms.typed_send(client.writer, message.Message.SHARE_UNMOUNT, {'drive': drive}))
        await asyncio.gather(*coros)

    async def run_task(self,
                       tsk_type: str|task.Type,
                       payload: str,
                       clients: str | int | list[int],
                       payload_type='text',
                       cwd: str=None,
                       env: dict=None,
                       interactive=False,
                       python_unbuf=False):
        tsk_type = task.Type.get(tsk_type)
        # clients has a special value '*' which means all clients
        if clients=='*':
            with self.clients_lock:
                clients = [c for c in self.clients if self.clients[c].online]
        elif isinstance(clients, int):
            clients = [clients]
        # check clients. must be known and online
        for c in clients:
            if c not in self.clients:
                raise ValueError(f'client with id {c} is not known')
            if tsk_type!=task.Type.Wake_on_LAN and not self.clients[c].online:
                raise ValueError(f'client with id {c} ({self.clients[c].name}) is not online')
        if not clients:
            # nothing to do
            return None, None

        # handle payload
        match payload_type:
            case 'text':
                pass    # nothing to do, payload already in payload variable
            case 'file':
                payload = await aiopath.AsyncPath(payload).read_text()

        # make task group
        task_group = task.create_group(tsk_type, payload, clients, cwd=cwd, env=env, interactive=interactive, python_unbuf=python_unbuf)

        # execute
        return await self.execute_task_group(task_group)

    async def execute_task_group(self, task_group: task.TaskGroup):
        self.task_groups[task_group.id] = task_group

        # start tasks
        launch_as_group = task.task_group_launch_as_group(task_group)
        coros = []
        for c in task_group.tasks:  # NB: index is client ID
            mytask = task_group.tasks[c]
            # add to client task list
            if self.clients[c].online:
                self.clients[c].online.tasks[mytask.id] = mytask
            if not launch_as_group:
                # send
                coros.append(task.send(mytask, self.clients[c]))
        if launch_as_group:
            coros.append(task.send(task_group, self.clients))

        await asyncio.gather(*coros)

        # return TaskGroup.id and [Task.id, ...] for all constituent tasks
        return task_group.id, [task_group.tasks[c].id for c in task_group.tasks]


    async def get_client_drives(self, client: structs.Client):
        if not client.online:
            return
        await comms.typed_send(client.online.writer, message.Message.FILE_GET_DRIVES)

    async def get_client_file_listing(self, client: structs.Client, path: str|pathlib.Path):
        if not client.online:
            return
        await comms.typed_send(client.online.writer, message.Message.FILE_GET_LISTING,
                               {'path': path})

    async def get_client_remote_shares(self, client: structs.Client, net_name: str, user: str = 'Guest', password: str = '', domain: str = ''):
        # list shares on specified target machine that are accessible from this client
        if not client.online:
            return
        await comms.typed_send(client.online.writer, message.Message.FILE_GET_SHARES,
                               {'net_name': net_name.strip('\\/'),  # support SERVER, \\SERVER, \\SERVER\, //SERVER and //SERVER/
                                'user': user,
                                'password': password,
                                'domain': domain})

    async def _send_file_action(self, client: structs.Client, action: message.Message, msg: dict[str, str]):
        if not client.online:
            return None # no action launched
        # add action id to message
        msg['action_id'] = self._file_action_id_provider.get_next()
        msg['action'] = action
        # send
        await comms.typed_send(client.online.writer, action, msg)
        # store locally as a pending action
        action_id = msg.pop('action_id')
        msg['status'] = structs.Status.Pending
        client.online.file_actions[action_id] = msg
        # return action's id
        return action_id
    async def _make_client_file_folder(self, client: structs.Client, path: str|pathlib.Path, exist_ok: bool, is_dir: bool):
        return await self._send_file_action(client, message.Message.FILE_MAKE,
                                            {'path': path,
                                             'exist_ok': exist_ok,
                                             'is_dir': is_dir})
    async def make_client_file  (self, client: structs.Client, path: str|pathlib.Path, exist_ok: bool = False):
        return await self._make_client_file_folder(client, path, exist_ok, False)
    async def make_client_folder(self, client: structs.Client, path: str|pathlib.Path, exist_ok: bool = False):
        return await self._make_client_file_folder(client, path, exist_ok, True)

    async def rename_client_file_folder(self, client: structs.Client, old_path: str|pathlib.Path, new_path: str|pathlib.Path):
        return await self._send_file_action(client, message.Message.FILE_RENAME,
                                            {'old_path': old_path,
                                             'new_path': new_path})

    async def _copy_move_client_file_folder(self, client: structs.Client, source_path: str|pathlib.Path, dest_path: str|pathlib.Path, dirs_exist_ok: bool, is_move: bool):
        return await self._send_file_action(client, message.Message.FILE_COPY_MOVE,
                                            {'source_path': source_path,
                                             'dest_path': dest_path,
                                             'dirs_exist_ok': dirs_exist_ok,    # NB: applies only to copy of a directory, not to copy of file or move of anything
                                             'is_move': is_move})
    async def copy_client_file_folder(self, client: structs.Client, source_path: str|pathlib.Path, dest_path: str|pathlib.Path, dirs_exist_ok: bool = False):
        return await self._copy_move_client_file_folder(client, source_path, dest_path, dirs_exist_ok, False)
    async def move_client_file_folder(self, client: structs.Client, source_path: str|pathlib.Path, dest_path: str|pathlib.Path):
        return await self._copy_move_client_file_folder(client, source_path, dest_path, False, True)

    async def delete_client_file_folder(self, client: structs.Client, path: str|pathlib.Path):
        return await self._send_file_action(client, message.Message.FILE_DELETE,
                                            {'path': path})


    async def toems_get_computers(self) -> list[dict[str,Any]]:
        with self.clients_lock:
            names = [self.clients[c].name for c in self.clients]
        if not self.toems:
            return []
        return await self.toems.computer_get(filter_list=names)

    async def toems_get_disk_images(self) -> list[dict[str,Any]]:
        if not self.toems:
            return []
        return await self.toems.image_get(project=self.project, project_format=config.master['toems']['images']['format'], name_mapping=config.master['base_image_name_table'] if 'base_image_name_table' in config.master else None)

    async def toems_get_disk_image_size(self, name_or_id: int|str):
        if not self.toems:
            return None
        return await self.toems.image_get_server_size(name_or_id)

    async def toems_get_disk_image_info(self, name_or_id: int|str):
        if not self.toems:
            return None
        image = (await self.toems.image_get(name_or_id))

        # get timestamp last time image was updated
        if not self.toems:
            return None
        im_logs = await self.toems.image_get_audit_log(image['Id'])
        for l in im_logs:   # NB: logs are sorted newest-first
            if l['AuditType'] in ['Upload','OndUpload']:
                upload_info = json.loads(l['ObjectJson'])
                if not self.toems:
                    return None
                computer = await self.toems.computer_get(upload_info['ComputerId'])
                return {
                    'TimeStamp': l['DateTime'],
                    'SourceComputer': computer['Name']
                }
        return None     # no info found

    async def toems_create_disk_image(self, name: str, description: str|None = None):
        if not self.admin:
            return None
        return await self.admin.create_image(name, description)

    async def toems_update_disk_image(self, name: str, updates):
        if not self.toems:
            return None
        image_id = (await self.toems.image_get(name))['Id']
        if not self.admin:
            return None
        return await self.admin.update_image(image_id, updates)

    async def toems_delete_disk_image(self, name: str) -> None:
        if not self.toems:
            return
        image_id = (await self.toems.image_get(name))['Id']
        if not self.admin:
            return
        await self.admin.delete_image(image_id)

    async def toems_deploy_disk_image(self, image: str, part_of_project: bool, clients: int|list[int]) -> None:
        if not self.toems:
            return
        image_id = (await self.toems.image_get(image))['Id']

        # update image info script
        if 'image_info_script' in config.master['toems']:
            im_info = await self.toems_get_disk_image_info(image_id)
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
            if not self.admin:
                return
            resp = await self.admin.image_set_script(image_id, config.master['toems']['image_info_script'], script, priority=1, run_when=3)
            if not resp['Success']:
                raise RuntimeError(f"can't deploy: failed to set image info script ({resp['ErrorMessage']})")
        if 'pre_upload_script' in config.master['toems']:
            # if there is a pre-upload script, disable it
            if not self.admin:
                return
            resp = await self.admin.image_set_script(image_id, config.master['toems']['pre_upload_script'], '', priority=0, run_when=0)
            if not resp['Success']:
                raise RuntimeError(f"can't deploy: failed to disable image cleanup script ({resp['ErrorMessage']})")

        if not isinstance(clients,list):
            clients = [clients]
        if not self.toems:
            return
        comps = await asyncio.gather(*[self.toems.computer_get(self.clients[c].name) for c in clients])
        comp_ids = [c['Id'] for c in comps if c is not None]
        if not comp_ids:
            raise RuntimeError(f"can't deploy: none of the indicated clients are known to Toems")
        for c in comp_ids:
            if not self.admin:
                return
            resp = await self.admin.apply_image(image_id, c)
            if not resp['Success']:
                raise RuntimeError(f"can't deploy: failed to apply image to computer ({resp['ErrorMessage']})")

        if not self.toems:
            return
        resp = await self.toems.computer_deploy(image_id, comp_ids)
        if not resp['Success']:
            raise RuntimeError(f"can't deploy: failed to start task ({resp['ErrorMessage']})")

    async def toems_upload_to_disk_image(self, client: int, image: str) -> None:
        # we can only ever upload to an image belonging to this project, so check it is a project image
        if not image.startswith(self.project+'_'):
            image = self.project+'_'+image
        if not self.toems:
            return
        im = await self.toems.image_get(image)
        if im is None:
            raise RuntimeError(f"can't upload: image with name '{image}' not found")
        image_id = im['Id']

        if not self.toems:
            return
        comp = await self.toems.computer_get(self.client[client])
        if comp is None:
            raise RuntimeError(f"can't upload: computer with name '{self.client[client].name}' not found or not known to Toems")
        comp_id  = comp['Id']
        if not self.admin:
            return
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
            if not self.admin:
                return
            resp = await self.admin.image_set_script(image_id, config.master['toems']['image_info_script'], '', priority=1, run_when=0)
            if not resp['Success']:
                raise RuntimeError(f"can't upload: failed to unset image info script ({resp['ErrorMessage']})")

        if not self.admin:
            return
        resp = await self.admin.update_image(image_id, {"Protected": False})
        if resp['Protected']:   # check it worked
            raise RuntimeError(f"can't upload: failed to unprotect image ({resp['ErrorMessage']})")

        if not self.toems:
            return
        resp = await self.toems.computer_upload(comp_id, image_id)
        if not resp['Success']:
            raise RuntimeError(f"can't upload: failed to start task ({resp['ErrorMessage']})")

    async def toems_get_active_imaging_tasks(self, image_id: int|None = None) -> list[dict[str,str]]:
        if not self.toems:
            return []
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

    async def toems_cancel_active_imaging_task(self, task_id: int) -> None:
        if not self.toems:
            return
        resp = await self.toems.imaging_tasks_cancel_active(task_id)
        if not 'Success' in resp or not resp['Success']:
            raise RuntimeError(f"can't cancel active image task: failed because: {resp['ErrorMessage']}")



def _check_has_GUI():
    if 'imgui-bundle' not in {pkg.key for pkg in pkg_resources.working_set}:
        raise RuntimeError('You must install labManager-master with the [GUI] extra if you wish to use the GUI. Required dependencies for the GUI not available...')

class GUIContainer:
    def __init__(self):
        self.gui: labManager.GUI.master.MainGUI = None

# run GUI master - returns when GUI is closed
def run_GUI(master: Master|None = None, use_GUI_login=False, gui_ref: GUIContainer|None = None):
    _check_has_GUI()
    from labManager.GUI import master as master_GUI
    if getattr(sys, "frozen", False) and "nohide" not in sys.argv:
        import ctypes
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)

    gui = master_GUI.MainGUI(master, use_GUI_login)
    if gui_ref:
        gui_ref.gui = gui
    gui.run()
    if not master:
        # if GUI started its own master, clean up
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
        password = getpass(f'Password for {username}: ')
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