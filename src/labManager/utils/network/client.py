import asyncio
import traceback
import platform
from typing import List, Tuple

from .. import structs, task
from .  import comms, ifs, keepalive, message, ssdp

class Client:
    def __init__(self, network):
        self.network = network
        self.address = None
        self.name    = platform.node()

        self._handler_task: asyncio.Task = None

        self._task_list: List[asyncio.Task] = []

    async def start(self, server_addr: Tuple[str,int] = None):
        # 1. get interfaces we can work with
        interfaces = sorted(ifs.get_ifaces(self.network))

        # 2. discover master, if needed
        if not server_addr:
            # start SSDP client
            ssdp_client = ssdp.Client(address=interfaces[0], device_type=structs.SSDP_DEVICE_TYPE)
            await ssdp_client.start()
            # send search request and wait for reply
            responses = await ssdp_client.do_discovery()
            # stop SSDP client
            await ssdp_client.stop()
            # get ip and port for master from advertisement
            ip, _, port = responses[0].headers['HOST'].rpartition(':')
            port = int(port) # convert to integer
        else:
            ip,port = server_addr

        # 3. found master, connect to it
        self.reader, self.writer = await asyncio.open_connection(
            ip, port, local_addr=(interfaces[0],0))
        keepalive.set(self.writer.get_extra_info('socket'))
        self.address = self.writer.get_extra_info('sockname')

        # run connection handler
        self._handler_task = asyncio.create_task(self._handle_master())

    async def stop(self, timeout=2):
        for t in self._task_list:
            t.cancel()
        await asyncio.sleep(0)  # give cancellation a chance to be sent and processed
        self.writer.close()
        await asyncio.wait(
            self._task_list +
            [
                asyncio.create_task(self.writer.wait_closed()),
                self._handler_task
            ],
            timeout=timeout
        )

    def get_waiter(self):
        return self._handler_task

    async def _handle_master(self):
        type = None
        while type != message.Message.QUIT:
            try:
                type, msg = await comms.typed_receive(self.reader)
                if not type:
                    # connection broken, close
                    break

                match type:
                    case message.Message.IDENTIFY:
                        await comms.typed_send(self.writer, message.Message.IDENTIFY, self.name)
                    case message.Message.INFO:
                        print(f'client {self.name} received: {msg}')

                    case message.Message.TASK_CREATE:
                        self._task_list.append(
                            asyncio.create_task(
                                task.Executor().run(msg['task_id'],msg['type'],msg['payload'], self.writer)
                            )
                        )

            except Exception as exc:
                tb_lines = traceback.format_exception(exc)
                print("".join(tb_lines))
                continue

        # remote connection closed, we're done
        self.writer.close()
