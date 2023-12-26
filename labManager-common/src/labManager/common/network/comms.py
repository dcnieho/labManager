import asyncio
import struct

from .. import message


async def _read_with_length(reader: asyncio.streams.StreamReader, decode: bool) -> str|bytes:
    # protocol: first the size of a message is sent so
    # receiver knows what to expect. Then the message itself
    # is sent
    try:
        try:
            msg_size = await reader.readexactly(message.SIZE_BYTES)
        except asyncio.IncompleteReadError:
            # connection broken
            return ''
        msg_size = struct.unpack(message.SIZE_FMT, msg_size)[0]

        msg = ''
        left_over = msg_size
        while left_over>0:
            received = (await reader.read(min(4096, left_over)))
            if not received:
                # connection broken
                return ''
            if decode:
                received = received.decode('utf8')
            msg += received
            left_over = msg_size-len(msg)
        return msg

    except ConnectionError:
        return ''
    except OSError as e:
        if e.errno in [113, 121]:   # 113: No route to host; 121: The semaphore timeout period has expired
            return ''

def prepare_transmission(msg: str|bytes) -> bytes:
    # notification of message length
    length = struct.pack(message.SIZE_FMT, len(msg))

    # msg itself
    if isinstance(msg, str):
        msg = msg.encode('utf8')

    return length + msg

async def send_with_length(writer: asyncio.streams.StreamWriter, msg: str|bytes) -> bool:
    if writer.is_closing():
        return False
    try:
        # get data to put on the line and send
        writer.write(prepare_transmission(msg))

        await writer.drain()
        return True
    except ConnectionError:
        return False


async def typed_receive(reader: asyncio.streams.StreamReader) -> tuple[message.Message,str]:
    # get message type
    msg_type = await _read_with_length(reader, True)
    if not msg_type:
        return None,''
    msg_type = message.Message.get(msg_type)

    # get associated data, if any
    msg = await _read_with_length(reader, message.type_map[msg_type]!=message.Type.BINARY)
    msg = message.parse(msg_type, msg)

    return msg_type, msg

async def typed_send(writer: asyncio.streams.StreamWriter, msg_type: message.Message, msg: str=''):
    # send message type
    await send_with_length(writer, msg_type.value)

    # send associated data, if any
    msg = message.prepare(msg_type, msg)
    await send_with_length(writer, msg)