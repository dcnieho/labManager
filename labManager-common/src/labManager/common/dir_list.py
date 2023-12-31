import pathlib
import mimetypes
import asyncio
import aiopath
import string

from . import structs

def get_drives() -> list[structs.DirEntry]:
    import ctypes
    drives = []
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    for letter in string.ascii_uppercase:
        if bitmask & 1:
            drive = f'{letter}:\\'
            entry = structs.DirEntry(drive,True,pathlib.Path(drive),None,None,None,None)
            # get name of drive
            drive_name = ctypes.create_unicode_buffer(1024)
            if not ctypes.windll.kernel32.GetVolumeInformationW(drive,drive_name,ctypes.sizeof(drive_name),None,None,None,None,0):
                raise ctypes.WinError(ctypes.get_last_error())
            entry.extra['drive_name'] = drive_name.value
            # get drive type
            match ctypes.windll.kernel32.GetDriveTypeW(drive):
                case 0 | 1:     # DRIVE_UNKNOWN, DRIVE_NO_ROOT_DIR
                    # we skip these
                    continue
                case 2:     # DRIVE_REMOVABLE
                    # like a USB drive
                    entry.mime_type = 'labManager/drive_removable'
                case 3:     # DRIVE_FIXED
                    entry.mime_type = 'labManager/drive'
                case 4:     # DRIVE_REMOTE
                    entry.mime_type = 'labManager/drive_network'
                    network_path = ctypes.create_unicode_buffer(1024)
                    ctypes.windll.mpr.WNetGetUniversalNameW(drive, 1, network_path, ctypes.sizeof(network_path)) # 1: UNIVERSAL_NAME_INFO_LEVEL
                    print(network_path)
                    todo    # let it crash
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
        stat, is_dir = await asyncio.gather(e.stat(), e.is_dir())
        out.append(structs.DirEntry(e.name, is_dir, pathlib.Path(e),
                                    stat.st_ctime, stat.st_mtime, stat.st_size,
                                    mimetypes.guess_type(e)[0]))

    return out