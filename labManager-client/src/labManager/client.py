import asyncio
import aioshutil
import aiopath
import traceback
import platform
import pathlib
import pathvalidate
import json
import threading
from dataclasses import dataclass, field

from labManager.common import async_thread, config, eye_tracker, file_actions, message, share, structs, task
from labManager.common.network import comms, ifs, keepalive, nmb, ssdp


__version__ = '0.9.0'


# main function for independently running client
async def run(duration: float = None):
    async_thread.setup()

    client = Client(config.client['network'])
    await client.start(keep_ssdp_running=True)

    # run
    if not duration:
        # wait forever
        await asyncio.Event().wait()
    else:
        await asyncio.sleep(duration)

    # shut down client if necessary, wait for it to quit
    await client.stop()

    async_thread.cleanup()


@dataclass
class ConnectedMaster:
    writer:         asyncio.streams.StreamWriter

    remote_addr:    tuple[str,int]
    local_addr:     tuple[str,int]

    handler:        asyncio.Task            = None

    task_list:      list[task.RunningTask]  = field(default_factory=list)
    mounted_drives: set[str]                = field(default_factory=set)

class Client:
    def __init__(self, network):
        self.network  = network
        self.name     = platform.node()

        self._ssdp_discovery_task:          asyncio.Task                = None
        self._ssdp_client:                  ssdp.Client                 = None
        self._if_ips:                       list[str]                   = None
        self._if_macs:                      list[str]                   = None

        self._netname_discoverer:           nmb.NetBIOSDiscovery        = None
        self._poll_for_eyetrackers_task:    asyncio.Task                = None
        self.connected_eye_tracker:         eye_tracker.ET_class        = None
        self._next_master_id:               int                         = 0
        self.masters:                       dict[int,ConnectedMaster]   = {}
        self.master_lock                                                = threading.Lock()

    def __del__(self):
        self._stop_sync()

    async def start(self, server_addr: tuple[str,int] = None, *, keep_ssdp_running = False):
        # 1. get interfaces we can work with
        for i in range(1,config.client['network_retry']['number_tries']+1):
            self._if_ips, self._if_macs = ifs.get_ifaces(self.network)
            if self._if_ips:
                break
            else:
                if i<config.client['network_retry']['number_tries']:
                    await asyncio.sleep(config.client['network_retry']['wait'])
                else:
                    raise RuntimeError(f'No interfaces found that are connected to the configured network {self.network}')

        # 2. start network name poller
        self._netname_discoverer = nmb.NetBIOSDiscovery(self.network, 30)
        await self._netname_discoverer.start()

        # 3. start eye tracker poller
        self._poll_for_eyetrackers_task = asyncio.create_task(self._poll_for_eyetrackers())

        # 4. discover master, if needed
        if not server_addr:
            # start SSDP client
            self._ssdp_client = ssdp.Client(
                address=self._if_ips[0],
                device_type=config.client['SSDP']['device_type'],
                response_handler=self._handle_ssdp_response if keep_ssdp_running else None,
                listen_to_notifications=True
            )
            await self._ssdp_client.start()
            # start discovery
            if keep_ssdp_running:
                # start discovery and keep running, connecting to anything
                # new thats found
                self._ssdp_discovery_task = await self._ssdp_client.discover_forever()
            else:
                # send search request and wait for reply
                responses,_ = await self._ssdp_client.do_discovery()
                # stop SSDP client
                await self._ssdp_client.stop()
                await self._handle_ssdp_response(responses[0])
        else:
            await self._start_new_master(server_addr)

    async def _handle_ssdp_response(self, response):
        # get ip and port for master from advertisement
        ip, _, port = response.headers['HOST'].rpartition(':')
        port = int(port) # convert to integer
        await self._start_new_master((ip,port))

    async def _start_new_master(self, server_addr: tuple[str,int]):
        # check if we're already connected to this master
        # if so, skip
        with self.master_lock:
            for m in self.masters:
                if server_addr==self.masters[m].remote_addr:
                    return

        # connect to master at specified server_address
        reader, writer = await asyncio.open_connection(
            *server_addr, local_addr=(self._if_ips[0],0))
        keepalive.set(writer.get_extra_info('socket'))
        m = self._get_next_master_id()
        with self.master_lock:
            self.masters[m] = \
                ConnectedMaster(writer, server_addr, writer.get_extra_info('sockname'))

        # run connection handler
        self.masters[m].handler = asyncio.create_task(self._handle_master(m, reader, writer))

    def _get_next_master_id(self):
        m = self._next_master_id
        self._next_master_id += 1
        return m

    async def stop(self, timeout=2):
        # stop and cancel everything
        self._stop_sync()
        await asyncio.sleep(0)  # give cancellation a chance to be sent and processed
        await self._netname_discoverer.stop()
        self._poll_for_eyetrackers_task.cancel()

        # wait till everything is stopped and cancelled
        with self.master_lock:
            running_tasks = [t.handler for m in self.masters for t in self.masters[m].task_list]
            close_waiters = [self.masters[m].writer.wait_closed() for m in self.masters]
            master_handlers = [self.masters[m].handler for m in self.masters]
        await asyncio.wait(
            running_tasks +
            [self._poll_for_eyetrackers_task] +
            ([self._ssdp_client.stop()] if self._ssdp_client else []) +
            close_waiters +
            master_handlers,
            timeout=timeout
        )

        # clear out state
        self._netname_discoverer = None
        self._poll_for_eyetrackers_task = None
        self.connected_eye_tracker = None
        self.masters = []

    def _stop_sync(self):
        # sync part of stopping
        if self._ssdp_discovery_task:
            self._ssdp_discovery_task.cancel()
        with self.master_lock:
            for m in self.masters:
                for t in self.masters[m].task_list:
                    t.handler.cancel()

                try:
                    self.masters[m].writer.close()
                except:
                    pass

    def get_waiters(self) -> list[asyncio.Task]:
        with self.master_lock:
            return [self.masters[m].handler for m in self.masters]

    async def broadcast(self, msg_type: str|message.Message, msg: str=''):
        msg_type = message.Message.get(msg_type)
        with self.master_lock:
            coros = [comms.typed_send(self.masters[m].writer, msg_type, msg) for m in self.masters]
        await asyncio.gather(*coros)

    def _remove_finished_task(self, m: int, my_task: asyncio.Task):
        if m not in self.masters:
            return
        for i,t in enumerate(self.masters[m].task_list):
            if t.handler.get_name()==my_task.get_name():
                del self.masters[m].task_list[i]
                break

    async def _handle_master(self, m: int, reader: asyncio.streams.StreamReader, writer: asyncio.streams.StreamWriter):
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
                        # check for image-info.json file in root
                        info_file = pathlib.Path('C:\\image_info.json')
                        info = None
                        if info_file.is_file():
                            with open(info_file) as f:
                                info = json.load(f)
                        await comms.typed_send(writer, message.Message.IDENTIFY, {'name': self.name, 'MACs': self._if_macs, 'image_info': info})

                    case message.Message.ET_STATUS_REQUEST:
                        if not self.connected_eye_tracker:
                            out = eye_tracker.Status.Not_connected
                        else:
                            out = eye_tracker.Status.Connected
                        await comms.typed_send(writer,
                                               message.Message.ET_STATUS_INFORM,
                                               {'status': out}
                                              )
                    case message.Message.ET_ATTR_REQUEST:
                        if not self.connected_eye_tracker:
                            out = None  # none means eye tracker not connected
                        else:
                            out = eye_tracker.get_attribute_message(self.connected_eye_tracker, msg)
                        await comms.typed_send(writer,
                                               message.Message.ET_ATTR_UPDATE,
                                               out
                                              )

                    case message.Message.SHARE_MOUNT:
                        share.mount_share(**msg)
                        self.masters[m].mounted_drives.add(msg['drive'])
                    case message.Message.SHARE_UNMOUNT:
                        share.unmount_share(**msg)
                        self.masters[m].mounted_drives.discard(msg['drive'])

                    case message.Message.TASK_CREATE:
                        new_task = task.RunningTask(msg['task_id'], )
                        new_task.handler = asyncio.create_task(
                            task.Executor().run(
                                msg['task_id'],msg['type'],msg['payload'],msg['cwd'],msg['env'],msg['interactive'],msg['python_unbuf'],
                                new_task,
                                writer)
                        )
                        self.masters[m].task_list.append(new_task)
                        new_task.handler.add_done_callback(lambda tsk: self._remove_finished_task(m, tsk))
                    case message.Message.TASK_INPUT:
                        # find if there is a running task with this id and which has an input queue, else ignore the input
                        my_task = None
                        for t in self.masters[m].task_list:
                            if msg['task_id']==t.id and not t.handler.done():
                                my_task = t
                                break
                        if my_task and my_task.input:
                            await my_task.input.put(msg['payload'])
                    case message.Message.TASK_CANCEL:
                        # find if there is a running task with this id, else ignore the request
                        my_task = None
                        for t in self.masters[m].task_list:
                            if msg['task_id']==t.id and not t.handler.done():
                                my_task = t
                                break
                        if my_task:
                            if my_task.input and not my_task.tried_stdin_close:
                                my_task.tried_stdin_close = True
                                await my_task.input.put(None)
                            else:
                                t.handler.cancel()

                    case message.Message.FILE_GET_DRIVES:
                        await comms.typed_send(writer,
                                               message.Message.FILE_LISTING,
                                               await _format_drives_file_listing_msg(file_actions.get_drives(), self._netname_discoverer.get_machines(as_direntry=True))
                                              )
                    case message.Message.FILE_GET_SHARES:
                        out = msg
                        msg['net_name'] = msg['net_name'].strip('\\/')  # support SERVER, \\SERVER, \\SERVER\, //SERVER and //SERVER/
                        try:
                            out['listing'] = file_actions.get_visible_shares(msg['net_name'], msg['user'], msg['password'], msg['domain'])
                        except Exception as exc:
                            msg['error'] = exc
                            msg['listing'] = []
                        del out['password']
                        out['path'] = f'//{out["net_name"]}/'
                        out['share_names'] = [s.name for s in out['listing']]
                        await comms.typed_send(writer,
                                               message.Message.FILE_LISTING,
                                               out
                                              )
                    case message.Message.FILE_GET_LISTING:
                        msg = {'path': str(msg['path'])}
                        try:
                            pathvalidate.validate_filepath(msg['path'], "auto")
                            msg['listing'] = await file_actions.get_dir_list(msg['path'])
                        except Exception as exc:
                            if isinstance(exc,pathvalidate.ValidationError):
                                exc = str(exc)  # these don't unpickle well, also can't assume receiver to have the same package installed
                            msg['error'] = exc
                            msg['listing'] = []
                        await comms.typed_send(writer,
                                               message.Message.FILE_LISTING,
                                               msg
                                              )

                    case message.Message.FILE_MAKE:
                        out = msg
                        out['status'] = structs.Status.Running
                        await comms.typed_send(writer,
                                               message.Message.FILE_ACTION_STATUS,
                                               out
                                              )

                        try:
                            if msg['is_dir']:
                                await file_actions.make_dir(msg['path'], msg['exist_ok'])
                            else:
                                await file_actions.make_file(msg['path'], msg['exist_ok'])
                        except Exception as exc:
                            if isinstance(exc,pathvalidate.ValidationError):
                                exc = str(exc)  # these don't unpickle well, also can't assume receiver to have the same package installed
                            out['error'] = exc
                            out['status'] = structs.Status.Errored
                        else:
                            out['status'] = structs.Status.Finished

                        await comms.typed_send(writer,
                                               message.Message.FILE_ACTION_STATUS,
                                               out
                                              )
                    case message.Message.FILE_RENAME:
                        out = msg
                        out['status'] = structs.Status.Running
                        await comms.typed_send(writer,
                                               message.Message.FILE_ACTION_STATUS,
                                               out
                                              )

                        try:
                            return_path = await file_actions.rename_path(msg['old_path'], msg['new_path'])
                        except Exception as exc:
                            if isinstance(exc,pathvalidate.ValidationError):
                                exc = str(exc)  # these don't unpickle well, also can't assume receiver to have the same package installed
                            out['error'] = exc
                            out['status'] = structs.Status.Errored
                        else:
                            out['return_path'] = pathlib.Path(return_path)
                            out['status'] = structs.Status.Finished

                        await comms.typed_send(writer,
                                               message.Message.FILE_ACTION_STATUS,
                                               out
                                              )
                    case message.Message.FILE_COPY_MOVE:
                        out = msg
                        out['status'] = structs.Status.Running
                        await comms.typed_send(writer,
                                               message.Message.FILE_ACTION_STATUS,
                                               out
                                              )

                        try:
                            if msg['is_move']:
                                return_path = await file_actions.move_path(msg['source_path'], msg['dest_path'])
                            else:
                                return_path = await file_actions.copy_path(msg['source_path'], msg['dest_path'], msg['dirs_exist_ok'])
                        except Exception as exc:
                            if isinstance(exc,pathvalidate.ValidationError):
                                exc = str(exc)  # these don't unpickle well, also can't assume receiver to have the same package installed
                            out['error'] = exc
                            out['status'] = structs.Status.Errored
                        else:
                            out['return_path'] = pathlib.Path(return_path)
                            out['status'] = structs.Status.Finished

                        await comms.typed_send(writer,
                                               message.Message.FILE_ACTION_STATUS,
                                               out
                                              )
                    case message.Message.FILE_DELETE:
                        out = msg
                        out['status'] = structs.Status.Running
                        await comms.typed_send(writer,
                                               message.Message.FILE_ACTION_STATUS,
                                               out
                                              )

                        try:
                            await file_actions.delete_path(msg['path'])
                        except Exception as exc:
                            if isinstance(exc,pathvalidate.ValidationError):
                                exc = str(exc)  # these don't unpickle well, also can't assume receiver to have the same package installed
                            out['error'] = exc
                            out['status'] = structs.Status.Errored
                        else:
                            out['status'] = structs.Status.Finished

                        await comms.typed_send(writer,
                                               message.Message.FILE_ACTION_STATUS,
                                               out
                                              )


            except Exception as exc:
                tb_lines = traceback.format_exception(exc)
                print("".join(tb_lines))
                continue

        # remote connection closed, we're done
        writer.close()

        # clean up any drives mounted by this master
        for drive in self.masters[m].mounted_drives:
            share.unmount_share(drive)

        # remove self from state
        with self.master_lock:
            if m in self.masters:
                del self.masters[m]

    async def _poll_for_eyetrackers(self):
        try:
            while True:
                # check if we have an eye tracker
                et = eye_tracker.get()
                if not self.connected_eye_tracker and et:
                    self.connected_eye_tracker = et
                    eye_tracker.subscribe_to_notifications(self.connected_eye_tracker, self.broadcast)
                    await self.broadcast(message.Message.ET_STATUS_INFORM, {'status': eye_tracker.Status.Connected})
                elif not et and self.connected_eye_tracker:
                    self.connected_eye_tracker = None
                    await self.broadcast(message.Message.ET_STATUS_INFORM, {'status': eye_tracker.Status.Not_connected})

                # rate-limit to every x seconds
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass    # we broke out of the loop: cancellation processed


async def _format_drives_file_listing_msg(drives: list[structs.DirEntry], net_names: list[tuple[structs.DirEntry,str]]):
    # get drives of this computer to add to the information
    out = {'path': 'root',
           'drives': [d.name for d in drives],
           'net_names': net_names}

    # format as a standard listing so its uniform for the receiver
    # use special mime-types to flag that the content is drives and network computers
    out['listing'] = drives.copy()
    for entry,_ in out['net_names']:
        # NB: //SERVER/ is the format pathlib understands and can concatenate share names to. It seems that this
        # isn't pickled and unpickled correctly over the network (indeed pathlib.Path(str(pathlib.Path('//SERVER/')))
        # is wrong). So send at plain strings that would be interpreted correctly by pathlib
        comp = str(entry.full_path).strip('\\/')
        entry.full_path = f'//{comp}/'
        out['listing'].append(entry)

    return out