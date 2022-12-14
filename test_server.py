import socket
import traceback
import threading
import struct
import time

import sys
import asyncio
import socket
import struct
import threading
import traceback

async def read_with_length(reader):
    # protocol: first the size of a message is sent so 
    # receiver knows what to expect. Then the message itself
    # is sent
    try:
        try:
            msg_size = await reader.readexactly(SIZE_MESSAGE_SIZE)
        except asyncio.IncompleteReadError:
            # connection broken
            return ''
        msg_size = struct.unpack(SIZE_MESSAGE_FMT, msg_size)[0]

        buf = ''
        left_over = msg_size
        while left_over>0:
            received = (await reader.read(min(4096, left_over))).decode('utf8')
            if not received:    # connection broken
                return ''
            buf += received
            left_over = msg_size-len(buf)
        return buf

    except ConnectionError:
        return 'ConnectionError'

async def send_with_length(writer, message) -> bool:
    try:
        to_send = message.encode('utf8')

        # first notify end point of message length
        writer.write(struct.pack(SIZE_MESSAGE_FMT, len(to_send)))

        # then send message
        writer.write(to_send)

        await writer.drain()
        return True
    except ConnectionError:
        return False

client_list = []
async def handle_client(reader, writer):
    sock = writer.get_extra_info('socket')
    set_keepalive_windows(sock)

    client_addr = writer.get_extra_info('peername')
    print('received connection from {}:{}'.format(*client_addr))
    
    message = None
    while message != 'quit':
        try:
            message = await read_with_length(reader)
            if message:
                print(f'<{client_addr[0]}:{client_addr[1]}> {message}')

                response = message+message
                if not await send_with_length(writer, response):
                    # connection broken, close
                    break
            else:
                # connection broken, close
                break
 
        except Exception as exc:
            tb_lines = traceback.format_exception(exc)
            print("".join(tb_lines))
            continue

    writer.close()

async_addr = None
async def run_server(server_address):
    global async_addr

    server = await asyncio.start_server(handle_client, *server_address)
    async_addr = [sock.getsockname() for sock in server.sockets]
    if len(async_addr[0])!=2:
        async_addr[0],async_addr[1] = async_addr[1],async_addr[0]
    print('serving on {}:{}'.format(*async_addr[0]))

    async with server:
        await server.serve_forever()    # docs say cancelling this coroutine causes server to stop




# to allow clients to discover server:
# Both connect to muticast on their configged subnet
# server sends periodic (1s?) announcements
# client stops listening once server found
# or look into what zeroconf does


def set_keepalive_linux(sock, after_idle_sec=1, interval_sec=3, max_fails=5):
    """Set TCP keepalive on an open socket.

    It activates after 1 second (after_idle_sec) of idleness,
    then sends a keepalive ping once every 3 seconds (interval_sec),
    and closes the connection after 5 failed ping (max_fails), or 15 seconds
    """
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, after_idle_sec)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, interval_sec)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, max_fails)

def set_keepalive_osx(sock, after_idle_sec=1, interval_sec=3, max_fails=5):
    """Set TCP keepalive on an open socket.

    sends a keepalive ping once every 3 seconds (interval_sec)
    """
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPALIVE, interval_sec) # socket.TCP_KEEPALIVE was added in 3.10

def set_keepalive_windows(sock, after_idle_sec=1, interval_sec=3, max_fails=5):
    """Set TCP keepalive on an open socket.

    It activates after after_idle_sec seconds of idleness, then
    sends a keepalive ping once every interval_sec seconds.
    On Windowds Vista and later, the connection is closed after
    10 failed ping attempts, see:
    https://learn.microsoft.com/en-us/windows/win32/winsock/sio-keepalive-vals
    """
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    if hasattr(socket,'TCP_KEEPIDLE'):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, after_idle_sec)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, interval_sec)
    else:
        sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, int(after_idle_sec*1000), int(interval_sec*1000)))

def set_keepalive(after_idle_sec=1, interval_sec=3, max_fails=5):
    if sys.platform.startswith("win"):
        set_keepalive_windows(after_idle_sec, interval_sec, max_fails)
    elif sys.platform.startswith("linux"):
        set_keepalive_linux(after_idle_sec, interval_sec, max_fails)
    elif sys.platform.startswith("darwin"):
        set_keepalive_osx(after_idle_sec, interval_sec, max_fails)
    else:
        print("Your system is not officially supported at the moment!\n"
              "You can let me know on GitHub, or you can try porting yourself ;)")
        sys.exit(1)

SIZE_MESSAGE_FMT  = '!i'
SIZE_MESSAGE_SIZE = struct.calcsize(SIZE_MESSAGE_FMT)
def send_message_with_length(sock: socket.socket, message) -> bool:
    try:
        to_send = bytes(message, 'utf-8')

        # first notify end point of message length
        sock.sendall(struct.pack(SIZE_MESSAGE_FMT, len(to_send)))

        # then send message
        sock.sendall(to_send)

        return True
    except ConnectionError:
        return False


def receive_message_with_length(sock: socket.socket) -> str:
    # protocol: first the size of a message is sent so 
    # receiver knows what to expect. Then the message itself
    # is sent
    try:
        msg_size = sock.recv(SIZE_MESSAGE_SIZE)
        if not msg_size:    # connection broken
            return ''
        msg_size = struct.unpack(SIZE_MESSAGE_FMT, msg_size)[0]

        buf = bytes('','utf-8')
        left_over = msg_size
        while left_over>0:
            received = sock.recv(min(4096, left_over))
            if not received:    # connection broken
                return ''
            buf += received
            left_over = msg_size-len(buf)

        return str(buf,'utf-8')
    except ConnectionError:
        return ''
    

def client(ip, port, message):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.connect((ip, port))
        send_message_with_length(sock, message)
        print("Received: {}".format(receive_message_with_length(sock)))


def await_clients(loop, futures):
    loop.run_until_complete(asyncio.gather(*futures))

if __name__ == "__main__":
    # Port 0 means to select an arbitrary unused port
    def run_loop(loop: asyncio.AbstractEventLoop):
        asyncio.set_event_loop(loop)
        try:
            loop.run_forever()
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=run_loop, args=(loop,), daemon=True)
    thread.start()
        
    asyncio.run_coroutine_threadsafe(run_server(("localhost", 0)), loop)

    while async_addr is None:
        time.sleep(.01)
    ip,port = async_addr[0]
    
    if True:
        aas = []
        aas.append(loop.run_in_executor(None, client, ip, port, "Hello World 1, more text"))
        aas.append(loop.run_in_executor(None, client, ip, port, "Hello World 2"))
        aas.append(loop.run_in_executor(None, client, ip, port, "Hello World 3"))
        
        done,pending = asyncio.run_coroutine_threadsafe(
                asyncio.wait(aas, return_when=asyncio.ALL_COMPLETED)
            , loop).result()

    else:
        ts = []
        ts.append(threading.Thread(target = client, args=[ip, port, "Hello World 1, more text"], daemon=True))
        ts.append(threading.Thread(target = client, args=[ip, port, "Hello World 2"], daemon=True))
        ts.append(threading.Thread(target = client, args=[ip, port, "Hello World 3"], daemon=True))
        for t in ts:
            t.start()

        for t in ts:
            t.join()