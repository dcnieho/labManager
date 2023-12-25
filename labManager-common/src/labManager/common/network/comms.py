import asyncio
import struct
from typing import Tuple

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

async def _send_with_length(writer: asyncio.streams.StreamWriter, msg: str|bytes, encode: bool) -> bool:
    try:
        if encode:
            msg = msg.encode('utf8')

        # first notify end point of message length
        writer.write(struct.pack(message.SIZE_FMT, len(msg)))

        # then send message, if anything
        if msg:
            writer.write(msg)

        await writer.drain()
        return True
    except ConnectionError:
        return False


async def typed_receive(reader: asyncio.streams.StreamReader) -> Tuple[message.Message,str]:
    type = await _read_with_length(reader, True)
    if not type:
        return None,''
    type = message.Message.get(type)

    msg = await _read_with_length(reader, message.type_map[type]!=message.Type.BINARY)
    msg = message.parse(type, msg)

    return type, msg

async def typed_send(writer: asyncio.streams.StreamWriter, type: message.Message, msg: str=''):
    await _send_with_length(writer, type.value, True)

    msg = message.prepare(type, msg)
    await _send_with_length(writer, msg, message.type_map[type]!=message.Type.BINARY)