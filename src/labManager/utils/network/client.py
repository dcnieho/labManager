import asyncio
import traceback
import platform
from typing import List, Tuple

from .. import structs, task
from .  import comms, ifs, keepalive, message, ssdp

class Client:
    def __init__(self, network):
        self.network  = network
        self.name     = platform.node()

        self._ssdp_discovery_task: asyncio.Task = None
        self._ssdp_client: ssdp.Client = None
        self._interfaces  = None

        self._handler_tasks    : List[asyncio.Task] = []
        self._connected_masters: List[Tuple[str,int]] = []
        self._local_addrs      : List[Tuple[str,int]] = []
        self._writers          : List[asyncio.streams.StreamWriter] = []

        self._task_list        : List[asyncio.Task] = []

    def __del__(self):
        self._stop_sync()

    async def start(self, server_addr: Tuple[str,int] = None, *, keep_ssdp_running = False):
        # 1. get interfaces we can work with
        self._interfaces = sorted(ifs.get_ifaces(self.network))

        # 2. discover master, if needed
        if not server_addr:
            # start SSDP client
            self._ssdp_client = ssdp.Client(
                address=self._interfaces[0],
                device_type=structs.SSDP_DEVICE_TYPE,
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
            *server_addr, local_addr=(self._interfaces[0],0))
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
        await asyncio.wait(
            self._task_list +
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
            t.cancel()
        if self._ssdp_discovery_task:
            self._ssdp_discovery_task.cancel()
        for w in self._writers:
            w.close()

    def get_waiters(self):
        return self._handler_tasks

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
                        await comms.typed_send(writer, message.Message.IDENTIFY, self.name)
                    case message.Message.INFO:
                        print(f'client {self.name} received: {msg}')

                    case message.Message.TASK_CREATE:
                        self._task_list.append(
                            asyncio.create_task(
                                task.Executor().run(msg['task_id'],msg['type'],msg['payload'],msg['cwd'],msg['env'], writer)
                            )
                        )

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
