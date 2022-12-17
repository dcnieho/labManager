import asyncio
import struct
import typing

from . import structs


SIZE_MESSAGE_FMT  = '!I'
SIZE_MESSAGE_SIZE = struct.calcsize(SIZE_MESSAGE_FMT)
async def read_with_length(reader) -> str:
    # protocol: first the size of a message is sent so 
    # receiver knows what to expect. Then the message itself
    # is sent
    try:
        try:
            msg_size = await reader.readexactly(SIZE_MESSAGE_SIZE)
        except asyncio.IncompleteReadError:
            # connection broken
            return None
        msg_size = struct.unpack(SIZE_MESSAGE_FMT, msg_size)[0]

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

async def receive_typed_message(reader) -> typing.Tuple[structs.Message,str]:
    type    = await read_with_length(reader)
    if not type:
        return None,''

    message = await read_with_length(reader)
    
    return structs.Message.get(type), message

async def send_with_length(writer, message) -> bool:
    try:
        to_send = message.encode('utf8')

        # first notify end point of message length
        writer.write(struct.pack(SIZE_MESSAGE_FMT, len(to_send)))

        # then send message, if anything
        if to_send:
            writer.write(to_send)

        await writer.drain()
        return True
    except ConnectionError:
        return False

async def send_typed_message(writer, type: structs.Message, message=''):
    await send_with_length(writer,type.value)
    await send_with_length(writer,message)