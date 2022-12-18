import asyncio
import dataclasses



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
