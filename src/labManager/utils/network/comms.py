import asyncio
import struct
from typing import Tuple

from .. import message


async def _read_with_length(reader: asyncio.streams.StreamReader) -> str:
    # protocol: first the size of a message is sent so
    # receiver knows what to expect. Then the message itself
    # is sent
    try:
        try:
            msg_size = await reader.readexactly(message.SIZE_BYTES)
        except asyncio.IncompleteReadError:
            # connection broken
            return None
        msg_size = struct.unpack(message.SIZE_FMT, msg_size)[0]

        msg = ''
        left_over = msg_size
        while left_over>0:
            received = (await reader.read(min(4096, left_over))).decode('utf8')
            if not received:
                # connection broken
                return ''
            msg += received
            left_over = msg_size-len(msg)
        return msg

    except ConnectionError:
        return ''

async def _send_with_length(writer: asyncio.streams.StreamWriter, msg: str) -> bool:
    try:
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
    type    = await _read_with_length(reader)
    if not type:
        return None,''
    type = message.Message.get(type)

    msg = await _read_with_length(reader)
    msg = message.parse(type, msg)

    return type, msg

async def typed_send(writer: asyncio.streams.StreamWriter, type: message.Message, msg: str=''):
    await _send_with_length(writer, type.value)

    msg = message.prepare(type, msg)
    await _send_with_length(writer, msg)