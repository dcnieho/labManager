import asyncio
import traceback
import platform

from .. import async_thread, structs, task
from .  import comms, ifs, keepalive, message, ssdp

class Client:
    def __init__(self, network):
        self.network = network
        self.address = None
        self.name    = platform.node()

        self._task: asyncio.Task = None

    async def start(self):
        # 1. get interfaces we can work with
        interfaces = sorted(ifs.get_ifaces(self.network))

        # 2. discover master
        # start SSDP client
        ssdp_client = ssdp.Client(address=interfaces[0], device_type=structs.SSDP_DEVICE_TYPE)
        await ssdp_client.start()
        # send search request and wait for reply
        responses = await ssdp_client.do_discovery()
        # stop SSDP client
        async_thread.run(ssdp_client.stop())
        # get ip and port for master from advertisement
        ip, _, port = responses[0].headers['HOST'].rpartition(':')
        port = int(port) # convert to integer

        # 3. found master, connect to it
        self.reader, self.writer = await asyncio.open_connection(
            ip, port, local_addr=(interfaces[0],0))
        keepalive.set(self.writer.get_extra_info('socket'))
        self.address = self.writer.get_extra_info('sockname')

        # run connection handler
        self._task = asyncio.create_task(self._handle_master())

    async def stop(self):
        self.writer.close()
        await self.writer.wait_closed()
        self._task = None

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
                        async_thread.run(task.execute(msg['task_id'],msg['type'],msg['payload'], self.writer))

            except Exception as exc:
                tb_lines = traceback.format_exception(exc)
                print("".join(tb_lines))
                continue

        # remote connection closed, we're done
        self.writer.close()
