import asyncio
import concurrent
import socket
import traceback
import platform

from .. import async_thread, structs, task
from .  import comms, ifs, keepalive, message, ssdp

class Client:
    def __init__(self, network):
        self.network = network
        self.address = None
        self.name    = platform.node()

        self._running_fut: concurrent.futures.Future = None

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
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind((interfaces[0],0))
        keepalive.set(sock)
        await async_thread.loop.sock_connect(sock, (ip, port))
        self.reader, self.writer = await asyncio.open_connection(sock=sock)
        self.address = sock.getsockname()

        return async_thread.run(self._loop())

    async def _loop(self):
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
                        print(f'client {id} received: {msg}')

                    case message.Message.TASK_CREATE:
                        async_thread.run(task.execute(msg['task_id'],msg['type'],msg['payload'], self.writer))

            except Exception as exc:
                tb_lines = traceback.format_exception(exc)
                print("".join(tb_lines))
                continue

        # remote connection closed, we're done
        self.writer.close()
