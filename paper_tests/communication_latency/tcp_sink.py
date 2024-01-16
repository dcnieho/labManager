# a bunch of the code here is taken from or adapted from https://github.com/dcnieho/labManager
import asyncio
import argparse
import pathlib
import platform
import numpy as np
import pandas as pd

import comms
import labManager.common.network.ifs as ifs
import labManager.common.network.keepalive as keepalive
import tcp_common


class Sink:
    def __init__(self, data_path, network):
        self.data_path = data_path
        self._handler_tasks     : list[asyncio.Task] = []
        self.sources            : dict[str, asyncio.streams.StreamWriter] = {}

        self.done_fut = asyncio.get_running_loop().create_future()

        self._if_ips, _ = ifs.get_ifaces(network)
        if not self._if_ips:
            raise RuntimeError(f'No interfaces found that are connected to the configured network {network}')
        self.me = platform.node()

    async def connect(self, source_addr: str, port: int):
        reader, writer = await asyncio.open_connection(
            source_addr, port, local_addr=(self._if_ips[0],0))
        sock = writer.get_extra_info('socket')
        keepalive.set(sock)

        # run connection handler
        self._handler_tasks.append(asyncio.create_task(self._handle_source(source_addr, reader, writer)))

    async def _handle_source(self, source_addr, reader: asyncio.streams.StreamReader, writer: asyncio.streams.StreamWriter):
        filename = ''
        tss = None
        last_idx = None
        connect_was_ok = False
        while True:
            try:
                msg, ts_received = await comms.receive(reader)
                if not msg:
                    # connection broken, close
                    break
                msg = msg.split(',')

                match msg[0]:
                    case 'accepted':
                        connect_was_ok = True
                        self._add_source(source_addr, writer)
                    case 'start':
                        filename = msg[2]   # msg[1] is timestamp
                        dur = int(msg[3])
                        freq= float(msg[4])
                        n_samp = int(dur*freq*1.01) # preallocate a little extra space
                        tss = np.empty((n_samp,2,), dtype=np.float64)
                        tss.fill(np.nan)
                        last_idx = -1
                        print(f'starting: {filename} ({dur}s @ {freq:.2f}Hz)')
                        await comms.send(writer, 'started')
                    case 'store':
                        last_idx = int(msg[1])
                        ts_sent = msg[2]
                        tss[last_idx,:] = [ts_sent, ts_received]
                    case 'done':
                        await comms.send(writer, 'saving')
                        # save to file
                        f_name = self.data_path / f'{self.me}_{filename}'
                        df = pd.DataFrame(tss[0:last_idx+1,:], columns = ['ts_sent', 'ts_received'])
                        df.index.name = 'idx'
                        df.to_csv(str(f_name), sep='\t', na_rep='nan', float_format="%.8f")
                        print(f'done: {filename}')
                        await comms.send(writer, 'done')
                    case 'quit':
                        break

            except Exception:
                continue

        # remote connection closed, we're done
        if connect_was_ok:
            self._remove_source(source_addr)
        writer.close()

    def _add_source(self, sid: str, writer: asyncio.streams.StreamWriter):
        print(f'connected to {sid} from {writer.get_extra_info("socket").getsockname()}')
        self.sources[sid] = writer

    def _remove_source(self, sid):
        del self.sources[sid]
        print(f'disconnected from {sid}')
        if not self.sources:
            print(f'all sources disconnected: we\'re done')
            self.done_fut.set_result(True)

async def run(port: int, data_path: pathlib.Path, sources: list[str], base_ip: str):
    sink = Sink(data_path, args.base_ip+'0/24')
    started = False
    for i in sources:
        ip = base_ip+i
        try:
            print(f'connecting to: {ip}:{port} ...', end='')
            await sink.connect(ip, port)
            started = True
        except:
            print('failed')
            pass
        else:
            print('success')

    # run
    if started:
        await asyncio.wait_for(sink.done_fut, timeout=None)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TCP data sink")
    parser.add_argument("sources", nargs="+", type=str)
    parser.add_argument("--allow_self", action='store_true')
    parser.add_argument("--base_ip", default='10.0.1.', type=str)
    parser.add_argument('-p',"--port", type=int, help="port to connect to server", default=tcp_common.SERVER_PORT)
    args = parser.parse_args()

    if_ips, _ = ifs.get_ifaces(args.base_ip+'0/24')
    if not args.allow_self:
        args.sources = [s for s in args.sources if args.base_ip+s not in if_ips]
    if not args.sources:
        print('No sources to await, exit')
        exit()

    data_path = pathlib.Path('.').resolve() / 'data'
    if not data_path.is_dir():
        data_path.mkdir()

    tcp_common.set_process_priority(tcp_common.ProcessPriority.HIGH_PRIORITY_CLASS)

    try:
        asyncio.run(run(args.port, data_path, args.sources, args.base_ip))
    except KeyboardInterrupt:
        pass