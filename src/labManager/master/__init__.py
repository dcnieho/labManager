import asyncio
import aiofile
import traceback
from typing import Dict, List, Tuple

from ..utils import async_thread, config, eye_tracker, message, network, structs, task


# main function for independently running master
# NB: requires that utils.async_thread has been set up
async def run(duration: float = None):
    from getpass import getpass
    username = input(f'Username: ')
    password = getpass(f'Password: ')
    # 1. check user credentials, and list projects they have access to
    client = network.admin_conn.Client(config.master['admin']['server'],config.master['admin']['port'])
    projects = await client.login(username, password)
    print('You have access to the following projects, which would you like to use?')
    for p in projects:
        print(f'  {p}')
    project = input(f'Project: ')
    if project not in projects:
        raise ValueError(f'project "{project}" not recognized, choose one of the projects you have access to: {projects}')
    client.set_project(project)
    await client.prep_toems()

    # 2. check we also have share access
    access = await client.check_share_access()

    # 3. log into toems server
    if True:
        toems = network.toems.Client(config.master['toems']['server'], config.master['toems']['port'], protocol='http')
        await toems.connect(username, password)

        image_list = await toems.image_get(project=project, project_format=config.master['toems']['images']['format'])
        if image_list:
            image = await toems.image_get(image_list[0]['Id'], project=project, project_format=config.master['toems']['images']['format'])
            print(image)

    # 4. start servers for listening to clients
    # get interfaces we can work with
    if_ips,_ = network.ifs.get_ifaces(config.master['network'])
    # start server to connect with clients
    server = Master()
    server.load_known_clients(config.master['clients'])
    async_thread.wait(server.start((if_ips[0], 0)))
    ip,port = server.address[0]

    # start SSDP server to advertise this server
    ssdp_server = network.ssdp.Server(
        address=if_ips[0],
        host_ip_port=(ip,port),
        usn="humlab-b055-master::"+config.master['SSDP']['device_type'],
        device_type=config.master['SSDP']['device_type'],
        allow_loopback=True)
    async_thread.wait(ssdp_server.start())

    # run
    if not duration:
        # wait forever
        await asyncio.Event().wait()
    else:
        await asyncio.sleep(duration)

    # stop servers
    async_thread.run(ssdp_server.stop()).result()
    async_thread.run(server.stop()).result()


class Master:
    def __init__(self):
        self.address = None

        self.clients: Dict[int, structs.Client] = {}
        self.known_clients: Dict[int, structs.KnownClient] = {}

        self.task_groups: Dict[int, task.TaskGroup] = {}

    def add_client(self, client: structs.Client):
        self.clients[client.id] = client

    def remove_client(self, client: structs.Client):
        self._remove_known_client(client)
        del self.clients[client.id]

    def load_known_clients(self, known_clients: List[Tuple[str,str]]):
        for client in known_clients:
            kc = structs.KnownClient(client['name'], client['MAC'])
            self.known_clients[kc.id] = kc

    def _find_known_client(self, client: structs.Client):
        for id in self.known_clients:
            if self.known_clients[id].MAC in client.MACs:
                client.known_client = self.known_clients[id]
                self.known_clients[id].client = client
                return

    def _remove_known_client(self, client: structs.Client):
        if client.known_client:
            client.known_client.client = None
        client.known_client = None

    async def start(self, local_addr: Tuple[str,int]):
        self.server = await asyncio.start_server(self._handle_client, *local_addr)

        addr = [sock.getsockname() for sock in self.server.sockets]
        if len(addr[0])!=2:
            addr[0], addr[1] = addr[1], addr[0]
        self.address = addr

        # should already have started serving in asyncio.start_server, but to be save and sure:
        await self.server.start_serving()

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()

    async def _handle_client(self, reader: asyncio.streams.StreamReader, writer: asyncio.streams.StreamWriter):
        network.keepalive.set(writer.get_extra_info('socket'))

        me = structs.Client(writer)
        self.add_client(me)

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
                        print(f'setting name for {me.host}:{me.port} to: {me.name}')
                        self._find_known_client(me)
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
        self.remove_client(me)

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