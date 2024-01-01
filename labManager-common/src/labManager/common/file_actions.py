import pathlib
import mimetypes
import asyncio
import aiopath
import aioshutil
import string
import pathvalidate

from . import structs

def get_thispc_listing() -> list[structs.DirEntry]:
    # TODO: not implemented
    import ctypes
    SHGFP_TYPE_CURRENT = 0   # Get current, not default value

    CSIDL_DESKTOP = 0
    CSIDL_MYDOCUMENTS = 5               # AKA CSIDL_PERSONAL
    CSIDL_DESKTOPDIRECTORY = 16
    CSIDL_DRIVES           = 17         # My Computer
    CSIDL_NETWORK          = 18
    

    buf= ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
    ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL_MYDOCUMENTS, None, SHGFP_TYPE_CURRENT, buf)

    entries = []
    entry = structs.DirEntry(drive[0:-1],True,pathlib.Path(drive),None,None,None,None)


def get_drives() -> list[structs.DirEntry]:
    import ctypes
    drives = []
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    for letter in string.ascii_uppercase:
        if bitmask & 1:
            drive = f'{letter}:\\'
            entry = structs.DirEntry(drive[0:-1],True,pathlib.Path(drive),None,None,None,None)
            # get name of drive
            drive_name = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
            if not ctypes.windll.kernel32.GetVolumeInformationW(drive,drive_name,ctypes.sizeof(drive_name),None,None,None,None,0):
                raise ctypes.WinError(ctypes.get_last_error())
            entry.extra['drive_name'] = drive_name.value
            # get drive type
            match ctypes.windll.kernel32.GetDriveTypeW(drive):
                case 0 | 1:     # DRIVE_UNKNOWN, DRIVE_NO_ROOT_DIR
                    # we skip these
                    continue
                case 2:     # DRIVE_REMOVABLE
                    # assume a USB drive, by far most likely. getting what it truly is seems complicated
                    entry.mime_type = 'labManager/drive_removable'
                    display_name = drive_name.value if drive_name.value else 'USB Drive'
                    entry.name = f'{display_name} ({drive[0:-1]})'
                case 3:     # DRIVE_FIXED
                    entry.mime_type = 'labManager/drive'
                    display_name = drive_name.value if drive_name.value else 'Local Disk'
                    entry.name = f'{display_name} ({drive[0:-1]})'
                case 4:     # DRIVE_REMOTE
                    entry.mime_type = 'labManager/drive_network'
                    network_path = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
                    ctypes.windll.mpr.WNetGetUniversalNameW(drive, 1, network_path, ctypes.sizeof(network_path)) # 1: UNIVERSAL_NAME_INFO_LEVEL
                    print(network_path)
                    todo    # let it crash
                    # TODO: display name should be 'share_name (net_name) (drive)'
                case 5:     # DRIVE_CDROM
                    entry.mime_type = 'labManager/drive_cdrom'
                case 6:     # DRIVE_RAMDISK
                    entry.mime_type = 'labManager/drive_ramdisk'
            # get size information
            total, free = ctypes.wintypes.ULARGE_INTEGER(), ctypes.wintypes.ULARGE_INTEGER()
            if not ctypes.windll.kernel32.GetDiskFreeSpaceExW(drive,None,ctypes.byref(total),ctypes.byref(free)):
                raise ctypes.WinError(ctypes.get_last_error())
            entry.size = total.value
            entry.extra['free_space'] = free.value

            drives.append(entry)
        bitmask >>= 1

    return drives

async def get_dir_list(path: pathlib.Path) -> list[structs.DirEntry]:
    # will throw when path doesn't exist or is not a directory
    path = aiopath.AsyncPath(path)
    out = []
    async for e in path.iterdir():
        try:
            stat, is_dir = await asyncio.gather(e.stat(), e.is_dir())
            item = structs.DirEntry(e.name, is_dir, pathlib.Path(e),
                                    stat.st_ctime, stat.st_mtime, stat.st_size,
                                    mimetypes.guess_type(e)[0])
        except:
            pass
        else:
            out.append(item)

    return out

async def make_dir(path: str | pathlib.Path):
    pathvalidate.validate_filepath(path, "auto")
    path = aiopath.AsyncPath(path)
    await path.mkdir()

async def make_file(path: str | pathlib.Path):
    pathvalidate.validate_filepath(path, "auto")
    path = aiopath.AsyncPath(path)
    await path.touch()

async def rename_path(old_path: str | pathlib.Path, new_path: str | pathlib.Path):
    pathvalidate.validate_filepath(old_path, "auto")
    pathvalidate.validate_filepath(new_path, "auto")
    return await aiopath.AsyncPath(old_path).rename(new_path)

async def copy_path(source_path: str | pathlib.Path, dest_path: str | pathlib.Path):
    pathvalidate.validate_filepath(source_path, "auto")
    pathvalidate.validate_filepath(dest_path, "auto")
    source_path = aiopath.AsyncPath(source_path)
    dest_path   = aiopath.AsyncPath(dest_path)
    if await source_path.is_dir():
        return await aioshutil.copytree(source_path, dest_path)
    else:
        return await aioshutil.copy2(source_path, dest_path)

async def move_path(source_path: str | pathlib.Path, dest_path: str | pathlib.Path):
    pathvalidate.validate_filepath(source_path, "auto")
    pathvalidate.validate_filepath(dest_path, "auto")
    source_path = aiopath.AsyncPath(source_path)
    dest_path   = aiopath.AsyncPath(dest_path)
    return await aioshutil.move(source_path, dest_path)

async def delete_path(path: str | pathlib.Path):
    pathvalidate.validate_filepath(path, "auto")
    path = aiopath.AsyncPath(path)
    if await path.is_dir():
        await aioshutil.rmtree(path, ignore_errors=True)
    else:
        await path.unlink()