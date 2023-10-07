# this is a mix of code from:
# - https://github.com/codingjoe/ssdp; and
# - https://github.com/MoshiBin/ssdpy
# and some other testing and googling to put it all together
#
# This code is absolutely not fully SSDP compliant.
# For instance, the USN the server returns is not
# standard compliant.
#
# But this does not matter. The server only replies to M-SEARCH
# requests with ST urn:schemas-upnp-org:device:labManager, not
# ssdp:all, and we do not send out periodic NOTIFYs, only one
# on startup of a new master. Therefore, our non-conformant
# announcements will not be seen often by any other networking
# equipment than our clients performing the discovery, so no
# other equipment can be confused by them.
#
# This hijacking of the SSDP protocol is just a nice and
# convenient way for clients to be able to find the server on
# the local network.

import asyncio
import errno
import socket
import struct
import time
import inspect

from ...common import async_thread

MULTICAST_ADDRESS_IPV4 = "239.255.255.250"
PORT = 1900
SSDP_NOTIFY_HEADER = "NOTIFY * HTTP/1.1"
SSDP_REQUEST_HEADER = "M-SEARCH * HTTP/1.1"
SSDP_RESPONSE_HEADER = "HTTP/1.1 200 OK"

class SSDPException(IOError):
    pass


class UnexpectedMessage(SSDPException):
    pass


class SSDPMessage:
    """Simplified HTTP message to serve as a SSDP message."""

    def __init__(self, version="HTTP/1.1", headers=None):
        if headers is None:
            headers = {}
        self.headers = headers

        self.version = version

    @classmethod
    def parse(cls, msg, verbose):
        """
        Parse message from string.

        Args:
            msg (str): Message string.

        Returns:
            SSDPMessage: Message parsed from string, or
            None if the message is not an SSDP message.

        """
        valid_headers = (
            SSDP_NOTIFY_HEADER,
            SSDP_REQUEST_HEADER,
            SSDP_RESPONSE_HEADER,
        )
        which = [msg.startswith(x) for x in valid_headers]
        if not any(which):
            if verbose:
                print(f"UnexpectedMessage: Invalid header: {msg}")
            return None

        lines = msg.splitlines()
        headers = {}
        # Skip the first line since it's just the HTTP return code
        for line in lines[1:]:
            if not line:
                break  # Headers and content are separated by a blank line
            if ":" not in line:
                if verbose:
                    print(f"Invalid line in header: {line}")
                return None
            header_name, header_value = line.split(":", 1)
            headers[header_name.strip().upper()] = header_value.strip()

        if which[0]:
            return SSDPNotify(lines[0], headers)
        elif which[1]:
            return SSDPRequest(lines[0], headers)
        else:
            return SSDPResponse(lines[0], headers)

    def __str__(self):
        """Return complete HTTP message."""
        raise NotImplementedError()

    def __bytes__(self):
        """Return complete HTTP message as bytes."""
        return self.__str__().encode().replace(b"\n", b"\r\n")

    def format_headers(self):
        lines = []
        for header in self.headers:
            lines.append("%s: %s" % (header, self.headers[header]))
        return lines

    async def sendto(self, transport, addr):
        """
        Send request/response to a given address via given transport.
        Args:
            transport (asyncio.DatagramTransport):
                Write transport to send the message on.
            addr (Tuple[str, int]):
                IP address and port pair to send the message to.
        """
        msg = bytes(self) + b"\r\n"
        transport.sendto(msg, addr)


class SSDPResponse(SSDPMessage):
    """Simple Service Discovery Protocol (SSDP) response."""

    def __init__(self, first_line=None, headers=None):
        if not first_line:
            first_line = SSDP_RESPONSE_HEADER
        version, status_code, reason = first_line.split()
        self.status_code = int(status_code)
        self.reason = reason
        super().__init__(version=version, headers=headers)

    def __str__(self):
        """Return complete SSDP response."""
        lines = []
        lines.append(" ".join([self.version, str(self.status_code), self.reason]))
        lines.extend(self.format_headers())
        return "\n".join(lines)


class SSDPRequestNotify(SSDPMessage):
    """Simple Service Discovery Protocol (SSDP) request."""

    def __init__(self, first_line, headers=None):
        method, uri, version = first_line.split()
        self.method = method
        self.uri = uri
        super().__init__(version=version, headers=headers)

    def __str__(self):
        """Return complete SSDP request."""
        lines = []
        lines.append(" ".join([self.method, self.uri, self.version]))
        lines.extend(self.format_headers())
        return "\n".join(lines)

class SSDPRequest(SSDPRequestNotify):
    """Simple Service Discovery Protocol (SSDP) request."""
    def __init__(self, first_line=None, headers=None):
        if not first_line:
            first_line = SSDP_REQUEST_HEADER
        super().__init__(first_line, headers=headers)

class SSDPNotify(SSDPRequestNotify):
    """Simple Service Discovery Protocol (SSDP) notification/advertisement."""
    def __init__(self, first_line=None, headers=None):
        if not first_line:
            first_line = SSDP_NOTIFY_HEADER
        super().__init__(first_line, headers=headers)


class SimpleServiceDiscoveryProtocol(asyncio.DatagramProtocol):
    """
    Simple Service Discovery Protocol (SSDP).

    SSDP is part of UPnP protocol stack. For more information see:
    https://en.wikipedia.org/wiki/Simple_Service_Discovery_Protocol
    """
    done = None

    def __init__(self,
                 is_server,
                 device_type,
                 usn=None,
                 advertised_host_ip_port=None,
                 respond_to_all=False,
                 response_notify_callback=None,
                 verbose=False):
        # if server, only respond to SSDP Requests
        # if not server (so client), only listen to responses
        self.is_server = is_server
        self.device_type = device_type
        # for server mode
        self.usn = usn
        self.advertised_host_ip_port = advertised_host_ip_port
        self.respond_to_all = respond_to_all
        # for client mode
        self.response_notify_callback = response_notify_callback

        # if verbose, print each message received, also those not acted upon
        self.verbose = verbose

        self.loop = asyncio.get_running_loop()
        self.done = self.loop.create_future()
        self.reply_tasks = set()

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        data = data.decode('utf8')

        msg = SSDPMessage.parse(data, self.verbose)
        if isinstance(msg, SSDPNotify):
            self.notification_received(msg, addr)
        elif isinstance(msg, SSDPResponse):
            self.response_received(msg, addr)
        elif isinstance(msg, SSDPRequest):
            self.request_received(msg, addr)
        else:
            pass

    def notification_received(self, notification: SSDPNotify, addr: tuple):
        """Handle an incoming notification."""
        if self.verbose:
            print(
                "received notification from {}: {}\n".format(addr, notification.headers['USN'])
            )
            print("header:\n{}\n".format("\n".join(notification.format_headers())))

        if not self.is_server and self.response_notify_callback:
            self.response_notify_callback(notification)

    def response_received(self, response: SSDPResponse, addr: tuple):
        """Handle an incoming response."""
        if self.verbose:
            print(
                "received response from {}: {} {} {}".format(
                    addr, response.status_code, response.reason, response.version
                )
            )
            print("header:\n{}\n".format("\n".join(response.format_headers())))

        if not self.is_server and self.response_notify_callback:
            self.response_notify_callback(response)

    def request_received(self, request: SSDPRequest, addr: tuple):
        """Handle an incoming request and respond to it."""
        if self.verbose:
            print(
                "received request from {}: {} {} {}".format(
                    addr, request.method, request.uri, request.version
                )
            )
            print("header:\n{}\n".format("\n".join(request.format_headers())))

        # If we're a server, check device type matches something we respond to.
        # If so, build response and send it.
        if not self.is_server:
            return
        if (request.headers["ST"]==self.device_type or (self.respond_to_all and request.headers["ST"]=="ssdp:all")):
            if self.verbose:
                print("Sending a response back to {}:{}:".format(*addr))
            ssdp_response = SSDPResponse(
                headers={
                    "Cache-Control": "max-age=30",
                    "Host": "{}:{}".format(*self.advertised_host_ip_port),
                    "Location": "",
                    "Server": "Python UPnP/1.0 SSDP",
                    "ST": self.device_type,
                    "NTS": "ssdp:alive",
                    "USN": self.usn,
                },
            )
            if self.verbose:
                print("header:\n{}\n".format(str(ssdp_response)))

            # fire off the reply task, and make sure it is kept alive until finished
            task = asyncio.create_task(ssdp_response.sendto(self.transport, addr))
            self.reply_tasks.add(task)
            task.add_done_callback(self.reply_tasks.discard)

    def error_received(self, exc):
        if exc == errno.EAGAIN or exc == errno.EWOULDBLOCK:
            pass
        else:
            raise IOError("Unexpected connection error") from exc

    def connection_lost(self, exc: Exception | None) -> None:
        if self.done:
            self.done.set_result(None)

class Base:
    def __init__(self,
                 is_server,
                 address,
                 device_type=None,
                 want_second=False,
                 verbose=False):
        self._is_server = is_server

        # set device type. If server, its the device type server will send
        # if client, its the device type set in the M-SEARCH message, and
        #            optionally also listen to notification messages on
        #            second socket
        if not device_type:
            if self._is_server:
                device_type = "ssdp:rootdevice"
            else:
                device_type = "ssdp:all"
        self.device_type = device_type
        self.address = address
        self._want_second = want_second
        self.verbose = verbose

        # for server mode
        self.usn = None
        self.advertised_host_ip_port = None
        self.respond_to_all = None

        self.loop = None
        self.transport_multicast = None # client optionally also has a multicast socket
        self.protocol_multicast = None
        self._is_started = False

    def _get_factory(self):
        raise NotImplementedError

    def _get_socket(self, which):
        raise NotImplementedError

    

    async def start(self):
        if self._is_started:
            return

        self.loop = asyncio.get_running_loop()
        sock = self._get_socket(1)
        ssdp_factory = self._get_factory()
        self.transport, self.protocol = \
            await self.loop.create_datagram_endpoint(ssdp_factory, sock=sock)
        if self._want_second:
            sock = self._get_socket(2)
            ssdp_factory = self._get_factory()
            self.transport_multicast, self.protocol_multicast = \
                await self.loop.create_datagram_endpoint(ssdp_factory, sock=sock)

        self._is_started = True

    def is_running(self):
        if self._is_started:
            if self.transport.is_closing() or self.protocol.done.done():
                self._is_started = False
        return self._is_started

    async def stop(self):
        if not self._is_started:
            return
        await self._stop()
        self.transport.close()
        if self._want_second:
            self.transport_multicast.close()
        if self.protocol:
            waiters = [self.protocol.done]
            if self._want_second:
                waiters.append(self.protocol_multicast.done)
            await asyncio.wait(waiters)
        self._is_started = False

    async def _stop(self):
        raise NotImplementedError

class Server(Base):
    def __init__(self, host_ip_port, usn, address='0.0.0.0', device_type=None, respond_to_all=False, allow_loopback=False, verbose=False):
        super().__init__(True, address, device_type, False, verbose)
        self.advertised_host_ip_port = host_ip_port
        self.usn = usn
        self.respond_to_all = respond_to_all
        self.allow_loopback = allow_loopback

    def _get_factory(self):
        return lambda: SimpleServiceDiscoveryProtocol(
            is_server=self._is_server,
            device_type=self.device_type,
            usn=self.usn,
            advertised_host_ip_port=self.advertised_host_ip_port,
            respond_to_all=self.respond_to_all,
            verbose=self.verbose
        )

    def _get_socket(self, which):
        return _get_multicast_socket(self.address, self.allow_loopback)

    async def send_notification(self):
        ssdp_notification = SSDPNotify(
            headers={
                "Cache-Control": "max-age=30",
                "HOST": "{}:{}".format(MULTICAST_ADDRESS_IPV4, PORT),
                "Location": "",
                "NT": self.device_type,
                "NTS": "ssdp:alive",
                "Server": "Python UPnP/1.0 SSDP",
                "USN": self.usn,
            },
        )
        await ssdp_notification.sendto(self.transport, (MULTICAST_ADDRESS_IPV4, PORT))

    async def _stop(self):
        pass

class Client(Base):
    def __init__(self, address='0.0.0.0', device_type=None, response_handler = None, listen_to_notifications = False, verbose=False):
        super().__init__(False, address, device_type, listen_to_notifications, verbose)
        self._responses      = []
        self._response_times = []
        self._response_fut   = None
        self._response_handler = response_handler
        self._response_handler_tasks = set()
        self._discovery_task = None

    def _get_factory(self):
        return lambda: SimpleServiceDiscoveryProtocol(
            is_server=self._is_server,
            device_type=self.device_type,
            response_notify_callback=self._process_response_notify,
            verbose=self.verbose
        )

    def _get_socket(self, which):
        if which==1:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.address, 0))
        else:
            sock = _get_multicast_socket(self.address, False)
        return sock

    def _process_response_notify(self, message):
        if isinstance(message,SSDPNotify):
            # got a notification, immediate issue request to get more info
            # about the service, if its relevant
            if message.headers['NT']==self.device_type:
                async_thread.run(self._send_request())
        else:
            self._store_response(message)

    def _store_response(self, response):
        t = time.perf_counter()
        # check if we've seen this host already
        is_duplicate = False
        for i,r in enumerate(self._responses):
            if r.headers['HOST']==response.headers['HOST']:
                # yup, its known. just update time it was seen
                self._response_times[i] = t
                is_duplicate = True
        # if not, add to list of hosts
        if not is_duplicate:
            self._responses.append(response)
            self._response_times.append(t)

            if self._response_fut and not self._response_fut.done():
                self._response_fut.set_result(None)

        # if user defined a response handler, always call it
        if self._response_handler:
            res = self._response_handler(response)
            # if awaitable, make sure its scheduled
            if inspect.isawaitable(res):
                task = asyncio.create_task(res)
                self._response_handler_tasks.add(task)
                task.add_done_callback(self._response_handler_tasks.discard)

    async def _send_request(self):
        ssdp_response = SSDPRequest(
            headers={
                "HOST": "{}:{}".format(MULTICAST_ADDRESS_IPV4, PORT),
                "MAN": "ssdp:discover",
                "MX": "1",
                "ST": self.device_type,
            },
        )
        await ssdp_response.sendto(self.transport, (MULTICAST_ADDRESS_IPV4, PORT))

    async def _stop(self):
        if self._response_fut and not self._response_fut.done():
            self._response_fut.set_result(None)
        if self._discovery_task and not self._discovery_task.done():
            self._discovery_task.cancel()

    def get_responses(self):
        t1 = time.perf_counter()
        return self._responses, [t1-t0 for t0 in self._response_times]

    async def _discovery_loop(self, interval):
        # periodically send discovery request
        # until cancelled
        try:
            while True:
                await self._send_request()
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass    # we broke out of the loop: cancellation processed

    async def discover_forever(self, interval=10):
        # to stop, cancel the returned future
        assert self._is_started, "the client must be start()ed before starting discovery"

        if not self._response_fut or self._response_fut.done():
            self._response_fut = self.loop.create_future()

        self._discovery_task = asyncio.create_task(self._discovery_loop(interval))
        return self._discovery_task

    async def do_discovery(self, interval=10):
        # periodically send discovery request (using discover_forever)
        # and wait until any replies received
        discovery_task = await self.discover_forever(interval)
        await asyncio.wait_for(self._response_fut, timeout=None)
        # we have a response, stop discovery and return it
        discovery_task.cancel()
        return self.get_responses()
    
def _get_multicast_socket(address, allow_loopback):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    mreq = socket.inet_aton(MULTICAST_ADDRESS_IPV4)
    if address is not None:
        mreq += socket.inet_aton(address)
    else:
        mreq += struct.pack(b"@I", socket.INADDR_ANY)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1 if allow_loopback else 0)
    sock.bind(("0.0.0.0", PORT))
    return sock