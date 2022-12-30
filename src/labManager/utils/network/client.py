import asyncio
import traceback
import platform
from typing import List, Tuple

from .. import config, eye_tracker, task
from .  import comms, ifs, keepalive, message, ssdp

class Client:
    def __init__(self, network):
        self.network  = network
        self.name     = platform.node()

        self._ssdp_discovery_task: asyncio.Task = None
        self._ssdp_client: ssdp.Client = None
        self._if_ips  = None
        self._if_macs = None

        self._connected_eye_tracker: eye_tracker.ET_class = None

        self._handler_tasks    : List[asyncio.Task] = []
        self._connected_masters: List[Tuple[str,int]] = []
        self._local_addrs      : List[Tuple[str,int]] = []
        self._writers          : List[asyncio.streams.StreamWriter] = []

        self._task_list        : List[task.RunningTask] = []

    def __del__(self):
        self._stop_sync()

    async def start(self, server_addr: Tuple[str,int] = None, *, keep_ssdp_running = False):
        # 1. get interfaces we can work with
        self._if_ips, self._if_macs = ifs.get_ifaces(self.network)

        # 2. discover master, if needed
        if not server_addr:
            # start SSDP client
            self._ssdp_client = ssdp.Client(
                address=self._if_ips[0],
                device_type=config.client['SSDP']['device_type'],
                response_handler=self._handle_ssdp_response if keep_ssdp_running else None
            )
            await self._ssdp_client.start()
            # start discovery
            if keep_ssdp_running:
                # start discovery and keep running, connecting to anything
                # new thats found
                self._ssdp_discovery_task = await self._ssdp_client.discover_forever(interval=1)
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

    async def _start_new_master(self, server_addr: Tuple[str,int]):
        # check if we're already connected to this master
        # if so, skip
        for i,m in enumerate(self._connected_masters):
            if server_addr==m:
                return

        # connect to master at specified server_address, connect to it
        reader, writer = await asyncio.open_connection(
            *server_addr, local_addr=(self._if_ips[0],0))
        keepalive.set(writer.get_extra_info('socket'))
        self._connected_masters.append(server_addr)
        self._local_addrs.append(writer.get_extra_info('sockname'))
        self._writers.append(writer)

        # run connection handler
        self._handler_tasks.append(asyncio.create_task(self._handle_master(reader, writer)))

    async def stop(self, timeout=2):
        # stop and cancel everything
        self._stop_sync()
        await asyncio.sleep(0)  # give cancellation a chance to be sent and processed
        writer_close_waiters = [asyncio.create_task(w.wait_closed()) for w in self._writers]

        # wait till everything is stopped and cancelled
        running_tasks = [x.async_task for x in self._task_list]
        await asyncio.wait(
            running_tasks +
            ([asyncio.create_task(self._ssdp_client.stop())] if self._ssdp_client else []) +
            writer_close_waiters + 
            self._handler_tasks,
            timeout=timeout
        )

        # clear out state
        self._handler_tasks = []
        self._connected_masters = []
        self._local_addrs = []
        self._writers = []
        self._task_list = []

    def _stop_sync(self):
        # sync part of stopping
        for t in self._task_list:
            t.async_task.cancel()
        if self._ssdp_discovery_task:
            self._ssdp_discovery_task.cancel()
        for w in self._writers:
            w.close()

    def get_waiters(self):
        return self._handler_tasks

    def _remove_finished_task(self, my_task: asyncio.Task):
        for i,t in enumerate(self._task_list):
            if t.async_task.get_name()==my_task.get_name():
                del self._task_list[i]
                break

    async def _handle_master(self, reader: asyncio.streams.StreamReader, writer: asyncio.streams.StreamWriter):
        type = None
        while type != message.Message.QUIT:
            try:
                type, msg = await comms.typed_receive(reader)
                if not type:
                    # connection broken, close
                    break
                
                match type:
                    case message.Message.IDENTIFY:
                        await comms.typed_send(writer, message.Message.IDENTIFY, {'name': self.name, 'MACs': self._if_macs})
                    case message.Message.INFO:
                        print(f'client {self.name} received: {msg}')

                    case message.Message.ET_ATTR_REQUEST:
                        if not self._connected_eye_tracker:
                            self._connected_eye_tracker = eye_tracker.get()
                            if self._connected_eye_tracker:
                                eye_tracker.subscribe_to_notifications(self._connected_eye_tracker, writer)
                                msg = '*'   # override requested: send all attributes
                        
                        await comms.typed_send(writer,
                                               message.Message.ET_ATTR_UPDATE,
                                               eye_tracker.get_attribute(self._connected_eye_tracker, msg)
                                              )

                    case message.Message.TASK_CREATE:
                        new_task = task.RunningTask(msg['task_id'])
                        new_task.async_task = asyncio.create_task(
                            task.Executor().run(
                                msg['task_id'],msg['type'],msg['payload'],msg['cwd'],msg['env'],msg['interactive'],
                                new_task,
                                writer)
                        )
                        self._task_list.append(new_task)
                        new_task.async_task.add_done_callback(self._remove_finished_task)

                    case message.Message.TASK_INPUT:
                        # find if there is a running task with this id and which has an input queue, else ignore the input
                        my_task = None
                        for t in self._task_list:
                            if msg['task_id']==t.id and not t.async_task.done():
                                my_task = t
                        if my_task and my_task.input:
                            await my_task.input.put(msg['payload'])

            except Exception as exc:
                tb_lines = traceback.format_exception(exc)
                print("".join(tb_lines))
                continue

        # remote connection closed, we're done
        writer.close()

        # remove self from state
        for i,w in enumerate(self._writers):
            if w==writer:
                del self._connected_masters[i]
                del self._local_addrs[i]
                del self._writers[i]
                break
