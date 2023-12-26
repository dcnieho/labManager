import pathlib
import mimetypes
import asyncio
import aiopath
from dataclasses import dataclass


@dataclass
class DirEntry:
    name: str
    is_dir: bool
    full_path: pathlib.Path
    ctime: float
    mtime: float
    size: int
    mime_type: str


async def get_dir_list(path: pathlib.Path) -> list[DirEntry] | None:
    # return None if path doesn't exist or is not a directory
    path = aiopath.AsyncPath(path)
    if not await path.is_dir():
        return None

    out = []
    async for e in path.iterdir():
        stat, is_dir = await asyncio.gather(e.stat(), e.is_dir())
        out.append(DirEntry(e.name, is_dir, pathlib.Path(e),
                            stat.st_ctime, stat.st_mtime, stat.st_size,
                            mimetypes.guess_type(e)[0]))

    return out