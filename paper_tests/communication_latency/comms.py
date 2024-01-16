import asyncio
import struct

import win_precise_time as wpt

SIZE_FMT    = '!I'
SIZE_BYTES  = struct.calcsize(SIZE_FMT)

# NB: version of labManager.common.network.comms that is simplified and has timestamping added


async def _read_with_length(reader: asyncio.streams.StreamReader) -> str:
    # protocol: first the size of a message is sent so
    # receiver knows what to expect. Then the message itself
    # is sent
    try:
        try:
            msg_size = await reader.readexactly(SIZE_BYTES)
        except asyncio.IncompleteReadError:
            # connection broken
            return '', None
        msg_size = struct.unpack(SIZE_FMT, msg_size)[0]

        msg = ''
        left_over = msg_size
        while left_over>0:
            received = (await reader.read(min(4096, left_over))).decode('utf8')
            if not received:
                # connection broken
                return '', None
            msg += received
            left_over = msg_size-len(msg)
        return msg, wpt.time()

    except ConnectionError:
        return '', None
    except OSError as e:
        if e.errno in [113, 121]:   # 113: No route to host; 121: The semaphore timeout period has expired
            return '', None

async def _send_with_length(writer: asyncio.streams.StreamWriter, header: str, msg: str) -> bool:
    try:
        msg = f'{header},{wpt.time():.8f},{msg}'
        msg = msg.encode('utf8')

        # first notify end point of message length
        writer.write(struct.pack(SIZE_FMT, len(msg)))

        # then send message, if anything
        if msg:
            writer.write(msg)

        await writer.drain()
        return True
    except ConnectionError:
        return False


async def receive(reader: asyncio.streams.StreamReader) -> str:
    msg, ts = await _read_with_length(reader)
    return msg, ts

async def send(writer: asyncio.streams.StreamWriter, header: str, msg: str=''):
    await _send_with_length(writer, header, msg)