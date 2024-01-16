# a bunch of the code here is taken from or adapted from https://github.com/dcnieho/labManager
import asyncio
import argparse
import datetime
import random
import string
import threading

import win_precise_time as wpt

import comms
import labManager.common.async_thread as async_thread
import labManager.common.network.ifs as ifs
import labManager.common.network.keepalive as keepalive
import tcp_common



def sleep_ns(dur_ns, granularity_ns, ref_time=None, fun = None):
    # very precise sleep, in very precise duration substeps
    if ref_time is None:
        ref_time = wpt.time_ns()
    t_target = ref_time+dur_ns

    t_wakeup_ns = ref_time + granularity_ns
    slack = 400_000 # .4 ms
    i = 0
    while (t:=wpt.time_ns()) < t_target:
        t_wakeup_ns = min(t_target, t_wakeup_ns)
        if t_wakeup_ns-t > slack:
            wpt.sleep_until_ns(t_wakeup_ns - slack)
        wpt.hotloop_until_ns(t_wakeup_ns)

        if fun is not None and fun(i):
            break

        t_wakeup_ns += granularity_ns
        i += 1

class Source:
    def __init__(self):
        self.sinks: dict[tuple(str,int),asyncio.streams.StreamWriter] = {}

        self.states: dict[tuple(str,int),str] = {}
        self.state_waiter: threading.Event = threading.Event()
        self.state_to_await: int = ''

        self.done_fut = None

    async def start(self, port: int, network: str):
        if self.done_fut is None or self.done_fut.done():
            self.done_fut = asyncio.get_running_loop().create_future()

        if_ips,_ = ifs.get_ifaces(network)
        if not if_ips:
            raise RuntimeError(f'No interfaces found that are connected to the configured network {network}')
        local_addr = (if_ips[0], port)

        self.server = await asyncio.start_server(self._handle_sink, *local_addr)

        addr = [sock.getsockname() for sock in self.server.sockets]
        if len(addr[0])!=2:
            addr[0], addr[1] = addr[1], addr[0]
        self.address = addr
        print(f'listening on: {self.address[0]}')

        # should already have started serving in asyncio.start_server, but to be safe and sure:
        await self.server.start_serving()

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        self.server = None

    def set_waiter(self, state):
        self.state_waiter.clear()
        self.state_to_await = state
        # check if state already met
        if all([self.states[s]==self.state_to_await for s in self.states]):
            self.state_waiter.set()

    async def _handle_sink(self, reader: asyncio.streams.StreamReader, writer: asyncio.streams.StreamWriter):
        sock = writer.get_extra_info('socket')
        remote_addr = sock.getpeername()

        await comms.send(writer,'accepted')

        keepalive.set(sock)
        tcp_common.set_nagle(sock, False)

        self._add_sink(remote_addr, writer)

        # process incoming messages
        while True:
            try:
                msg, _ = await comms.receive(reader)
                if not msg:
                    # connection broken, close
                    break
                msg = msg.split(',')

                match msg[0]:
                    case 'started':
                        self.states[remote_addr] = 'started'
                    case 'finished':
                        self.states[remote_addr] = 'finished'
                    case 'saved':
                        self.states[remote_addr] = 'saved'
                if all([self.states[s]==self.state_to_await for s in self.states]):
                    self.state_waiter.set()
            except Exception:
                continue

        # remove from sink list and clean up
        self._remove_sink(remote_addr)
        writer.close()

    async def broadcast(self, header: str, msg: str = ''):
        coros = [comms.send(self.sinks[s], header, msg) for s in self.sinks]
        await asyncio.gather(*coros)

    def _add_sink(self, sid: int, writer: asyncio.streams.StreamWriter):
        self.sinks[sid] = writer
        self.states[sid] = 0
        print(f'{sid[0]} connected')

    def _remove_sink(self, sid):
        del self.sinks[sid]
        del self.states[sid]
        print(f'{sid[0]} disconnected')
        if not self.sinks and self.done_fut is not None:
            print(f'no clients left')
            self.done_fut.set_result(True)

def get_random_string(length):
    # choose from all lowercase letter
    letters = string.ascii_lowercase
    return ''.join(random.choice(letters) for _ in range(length))

filler = ''
def send_sample(source, idx, num_char):
    global filler
    header = f'store,{idx}'
    if len(filler) != num_char:
        filler = get_random_string(num_char)
    msg = f'{filler}'
    async_thread.run(source.broadcast(header,msg))
    return False

def create_filename(ips: tuple[str], idx: int, dur: int, num_char: int, freq: float):
    t = datetime.datetime.now()
    ip = ips[0].split('.')[-1]
    return f'SOURCE{ip}_{idx}_{t:%Y-%m-%d-%H-%M-%S}_{dur}_{num_char}_{freq:.2f}.tsv'

def check_sinks(source: Source, sinks: list[str], baseip: str):
    found = [False for _ in sinks]
    for i,ip in enumerate(sinks):
        for c in source.sinks:
            if c[0]==baseip+ip:
                found[i] = True
                break
    return all(found)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TCP data source")
    parser.add_argument("sinks", nargs="+", type=str)
    parser.add_argument("--allow_self", action='store_true')
    parser.add_argument("--base_ip", default='10.0.1.', type=str)
    parser.add_argument('-f','--frequency', type=float, help="sampling rate (Hz) of data to send", action='extend', nargs="+", required=True)
    parser.add_argument('-d','--duration', type=int, help="amount of data to send (seconds)", action='extend', nargs="+", required=True)
    parser.add_argument('-n',"--num_char", type=int, help="number of characters of data to append to each message", action='extend', nargs="+", required=True)
    parser.add_argument('-p',"--port", type=int, help="port to host server", default=tcp_common.SERVER_PORT)
    args = parser.parse_args()

    if_ips, _ = ifs.get_ifaces(args.base_ip+'0/24')

    if not args.allow_self:
        args.sinks = [c for c in args.sinks if args.base_ip+c not in if_ips]
    if not args.sinks:
        print('No sinks to await, exit')
        exit()
    print(f'expecting sinks: {", ".join([args.base_ip+c for c in sorted(args.sinks, key=int)])}')

    tcp_common.set_process_priority(tcp_common.ProcessPriority.HIGH_PRIORITY_CLASS)


    async_thread.setup()
    source = Source()
    sleep_granularity_ns = 500_000  # .5 millisecond

    try:
        async_thread.run(source.start(args.port, args.base_ip+'0/24'))
        print('started')

        while not check_sinks(source, args.sinks, args.base_ip):
            sleep_ns(5_000_000_000, sleep_granularity_ns, fun=lambda _: check_sinks(source, args.sinks, args.base_ip))
        print('all sinks connected, start')

        for idx,(freq, dur, num_char) in enumerate(zip(args.frequency, args.duration, args.num_char)):
            f_name = create_filename(if_ips, idx, dur, num_char, freq)
            async_thread.run(source.broadcast('start', f'{f_name},{dur},{freq:.4f}'))
            print(f'starting {dur}s @ {freq:.2f} Hz -> {f_name}')
            source.set_waiter('started')
            source.state_waiter.wait()

            # send x s of data at y Hz
            rate_wait = int(1/freq*1_000_000_000)
            sleep_ns(dur*1_000_000_000, rate_wait, fun=lambda idx: send_sample(source, idx, num_char))

            # indicate we're done sending, wait till all clients indicate they are done receiving
            async_thread.run(source.broadcast('finish'))
            print(f'finish: {f_name}')
            source.set_waiter('finished')
            source.state_waiter.wait()

            # trigger saving on clients, wait till they are all done before continuing
            async_thread.run(source.broadcast('save'))
            print(f'save: {f_name}')
            source.set_waiter('saved')
            source.state_waiter.wait()

        async_thread.run(source.broadcast('quit'))

        async_thread.wait(asyncio.wait_for(source.done_fut, timeout=None))
        async_thread.run(source.stop())
    except KeyboardInterrupt:
        pass
    async_thread.cleanup()