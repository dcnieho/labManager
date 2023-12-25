import pathlib
from dataclasses import dataclass


class CounterContext:
    count = -1      # so that first number is 0

    def __enter__(self):
        self._increment()
    async def __aenter__(self):
        self.__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.__exit__(exc_type, exc_val, exc_tb)

    def _increment(self):
        self.count += 1
    def get_next(self):
        self._increment()
        return self.count



_client_id_provider = CounterContext()
@dataclass
class Client:
    name        : str
    MACs        : list[str]
    id          : int = None
    known       : bool = False

    online      : 'labManager.master.ConnectedClient' = None

    def __post_init__(self):
        global _client_id_provider
        with _client_id_provider:
            self.id = _client_id_provider.count

    def __repr__(self):
        return f'{self.name}@{self.MACs}, {"" if self.online else "not "}connected'

@dataclass
class DirEntry:
    name: str
    is_dir: bool
    full_path: pathlib.Path
    ctime: float
    mtime: float
    size: int
    mime_type: str