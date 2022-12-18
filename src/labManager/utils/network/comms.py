import asyncio
import struct
from typing import Tuple

from . import constants


async def read_with_length(reader: asyncio.streams.StreamReader) -> str:
    # protocol: first the size of a message is sent so 
    # receiver knows what to expect. Then the message itself
    # is sent
    try:
        try:
            msg_size = await reader.readexactly(constants.SIZE_MESSAGE_SIZE)
        except asyncio.IncompleteReadError:
            # connection broken
            return None
        msg_size = struct.unpack(constants.SIZE_MESSAGE_FMT, msg_size)[0]

        buf = ''
        left_over = msg_size
        while left_over>0:
            received = (await reader.read(min(4096, left_over))).decode('utf8')
            if not received:
                # connection broken
                return ''
            buf += received
            left_over = msg_size-len(buf)
        return buf

    except ConnectionError:
        return ''

async def send_with_length(writer: asyncio.streams.StreamWriter, message: str) -> bool:
    try:
        to_send = message.encode('utf8')

        # first notify end point of message length
        writer.write(struct.pack(constants.SIZE_MESSAGE_FMT, len(to_send)))

        # then send message, if anything
        if to_send:
            writer.write(to_send)

        await writer.drain()
        return True
    except ConnectionError:
        return False
    

async def receive_typed_message(reader: asyncio.streams.StreamReader) -> Tuple[constants.Message,str]:
    type    = await read_with_length(reader)
    if not type:
        return None,''

    message = await read_with_length(reader)
    
    return constants.Message.get(type), message

async def send_typed_message(writer: asyncio.streams.StreamWriter, type: constants.Message, message: str=''):
    await send_with_length(writer,type.value)
    await send_with_length(writer,message)