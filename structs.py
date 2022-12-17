import asyncio
import dataclasses
import enum



@dataclasses.dataclass
class Client:
    writer: asyncio.streams.StreamWriter
    
    host: str = None
    port: int = None
    name: str = None

    def __post_init__(self):
        self.host,self.port = self.writer.get_extra_info('peername')

    def __repr__(self):
        return f'{self.name}@{self.host}:{self.port}'


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
