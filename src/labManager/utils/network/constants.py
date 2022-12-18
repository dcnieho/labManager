import struct
import enum

SIZE_MESSAGE_FMT  = '!I'
SIZE_MESSAGE_SIZE = struct.calcsize(SIZE_MESSAGE_FMT)


class Message(enum.Enum):
    QUIT = 'quit'
    IDENTIFY = 'identify'
    INFO = 'info'

    def get(message: str):
        if isinstance(message, Message):
            return message

        if isinstance(message, str) and message in [e.value for e in Message]:
            return Message(message)
        else:
            raise ValueError(f"The variable 'message' should be a string identifying one of the messages")