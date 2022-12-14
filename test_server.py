import socket
import selectors
import traceback
import threading
import struct
import time



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

class _ClientList(list):
    """
    List of all clients.
    """
    def append(self, client):
        self.reap()
        super().append(client)

    def pop_all(self):
        self[:], result = [], self[:]
        return result

    def join(self):
        for client in self.pop_all():
            client.thread.join()

    def reap(self):
        self[:] = (client for client in self if client.thread.is_alive())


class ThreadedTCPServer:
    def __init__(self, server_address):
        self.server_address = server_address
        self.__is_shut_down = threading.Event()
        self.__shutdown_request = False

        # TODO: this needs to be protected by a lock for operations on the clients
        self._clients = _ClientList()

        # start server
        self.socket: socket.socket = \
            socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind(self.server_address)
        self.server_address = self.socket.getsockname()
        self.socket.listen(20)

    def fileno(self):
        """Return socket file number.
        Interface required by selector.
        """
        return self.socket.fileno()
        
    def serve_forever(self, poll_interval=0.5):
        """Handle one request and poll for shutdown
        every poll_interval seconds.
        """
        self.__is_shut_down.clear()
        try:
            # XXX: Consider using another file descriptor or connecting to the
            # socket to wake this up instead of polling. Polling reduces our
            # responsiveness to a shutdown request and wastes cpu at all other
            # times.
            with selectors.DefaultSelector() as selector:
                selector.register(self, selectors.EVENT_READ)

                while not self.__shutdown_request:
                    ready = selector.select(poll_interval)
                    if self.__shutdown_request:
                        # shutdown() called during select(), exit immediately.
                        break
                    if ready:
                        self._handle_connection()

        finally:
            self.__shutdown_request = False
            self.__is_shut_down.set()

    def shutdown(self):
        """Stops the serve_forever loop.
        Blocks until the loop has finished. This must be called while
        serve_forever() is running in another thread, or it will
        deadlock.
        """
        self.__shutdown_request = True
        self.__is_shut_down.wait()

    def close(self):
        self.socket.shutdown(socket.SHUT_WR)
        self.socket.close()

    def _handle_connection(self):
        try:
            conn, client_address = self.socket.accept()
        except OSError:
            return

        set_keepalive_windows(conn)

        # start thread to serve the client
        t = ClientThread(conn, client_address)
        t.start()
        # add to client list (this also culls any dead clients from list)
        self._clients.append(t)

        
class ClientThread:
    def __init__(self, conn, client_address) -> None:
        self.conn = conn
        self.client_address = client_address
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.client_thread, daemon=True)
        self.thread.start()

    def client_thread(self):
        while True:
            try:
                message = self.receive()
                if message:
                    print (f'<{self.client_address[0]}:{self.client_address[1]}> {message}')

                    if not self.send('received'):
                        # connection broken, close
                        break
                else:
                    # connection broken, close
                    break
 
            except Exception as exc:
                tb_lines = traceback.format_exception(exc)
                print("".join(tb_lines))
                continue

        # we're done, shut down
        self.conn.shutdown(socket.SHUT_WR)
        self.conn.close()

    def receive(self) -> str:
        return receive_message_with_length(self.conn)

    def send(self, message):
        return send_message_with_length(self.conn, message)


def client(ip, port, message):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.connect((ip, port))
        send_message_with_length(sock, message)
        print("Received: {}".format(receive_message_with_length(sock)))

if __name__ == "__main__":
    # Port 0 means to select an arbitrary unused port
    HOST, PORT = "localhost", 0

    server = ThreadedTCPServer((HOST, PORT))
    ip, port = server.server_address

    # Start server thread. Server thread will start one thread to handle each client
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    
    threading.Thread(target = client, args=[ip, port, "Hello World 1, more text"], daemon=True).start()
    threading.Thread(target = client, args=[ip, port, "Hello World 2"], daemon=True).start()
    threading.Thread(target = client, args=[ip, port, "Hello World 3"], daemon=True).start()

    time.sleep(2)

    server.shutdown()