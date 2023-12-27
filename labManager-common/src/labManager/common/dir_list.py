import pathlib
import mimetypes
import asyncio
import aiopath
import string

from . import structs

async def get_drives():
    drives = []
    for letter in string.ascii_uppercase:
        drive = f"{letter}:\\"
        if await aiopath.AsyncPath(drive).exists():
            drives.append(pathlib.Path(drive))
    return drives

async def get_dir_list(path: pathlib.Path) -> list[structs.DirEntry] | None:
    # return None if path doesn't exist or is not a directory
    path = aiopath.AsyncPath(path)
    if not await path.is_dir():
        return None

    out = []
    async for e in path.iterdir():
        stat, is_dir = await asyncio.gather(e.stat(), e.is_dir())
        out.append(structs.DirEntry(e.name, is_dir, pathlib.Path(e),
                                    stat.st_ctime, stat.st_mtime, stat.st_size,
                                    mimetypes.guess_type(e)[0]))

    return out