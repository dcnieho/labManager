import pathlib
import mimetypes
import asyncio
import aiopath
import aioshutil
import string
import pathvalidate

from . import structs

import ctypes
_kernel32 = ctypes.WinDLL('kernel32',use_last_error=True)
_mpr = ctypes.WinDLL('mpr',use_last_error=True)


ERROR_NO_MORE_ITEMS = 259
ERROR_EXTENDED_ERROR= 1208
def _error_check_non0_is_error(result, func, args):
    if not result or result==ERROR_NO_MORE_ITEMS:
        return result
    if result==ERROR_EXTENDED_ERROR:
        last_error = ctypes.wintypes.DWORD()
        provider = ctypes.create_unicode_buffer(256)
        description = ctypes.create_unicode_buffer(256)
        WNetGetLastError(ctypes.byref(last_error),description,ctypes.sizeof(description),provider,ctypes.sizeof(provider))
        raise ctypes.WinError(last_error.value, f'{provider.value} failed: {description.value}')
    raise ctypes.WinError(ctypes.get_last_error())
def _error_check_0_is_error(result, func, args):
    if not result:
         raise ctypes.WinError(ctypes.get_last_error())

WNetGetLastError = _mpr.WNetGetLastErrorW
WNetGetLastError.argtypes = ctypes.wintypes.LPDWORD, ctypes.wintypes.LPWSTR, ctypes.wintypes.DWORD, ctypes.wintypes.LPWSTR, ctypes.wintypes.DWORD
WNetGetLastError.restype = ctypes.wintypes.DWORD
WNetGetLastError.errcheck = _error_check_non0_is_error

class UNIVERSAL_NAME_INFO(ctypes.Structure):
	_fields_ = [
		('universal_name', ctypes.wintypes.LPWSTR),
	]
LPUNIVERSAL_NAME_INFO = ctypes.POINTER(UNIVERSAL_NAME_INFO)
class REMOTE_NAME_INFO(ctypes.Structure):
	_fields_ = [
		('universal_name', ctypes.wintypes.LPWSTR),
		('connection_name', ctypes.wintypes.LPWSTR),
		('remaining_path', ctypes.wintypes.LPWSTR),
	]
LPREMOTE_NAME_INFO = ctypes.POINTER(REMOTE_NAME_INFO)
UNIVERSAL_NAME_INFO_LEVEL   = 0x00000001
REMOTE_NAME_INFO_LEVEL      = 0x00000002
WNetGetUniversalName = _mpr.WNetGetUniversalNameW
WNetGetUniversalName.argtypes = ctypes.wintypes.LPCWSTR, ctypes.wintypes.DWORD, ctypes.wintypes.LPVOID, ctypes.wintypes.LPDWORD
WNetGetUniversalName.restype = ctypes.wintypes.DWORD
WNetGetUniversalName.errcheck = _error_check_non0_is_error

GetLogicalDrives = _kernel32.GetLogicalDrives
GetLogicalDrives.argtypes = None
GetLogicalDrives.restype = ctypes.wintypes.DWORD
GetLogicalDrives.errcheck: _error_check_0_is_error

GetVolumeInformation = _kernel32.GetVolumeInformationW
GetVolumeInformation.argtypes = ctypes.wintypes.LPCWSTR, ctypes.wintypes.LPWSTR, ctypes.wintypes.DWORD, ctypes.wintypes.LPDWORD, ctypes.wintypes.LPDWORD, ctypes.wintypes.LPDWORD, ctypes.wintypes.LPWSTR, ctypes.wintypes.DWORD
GetVolumeInformation.restype = ctypes.wintypes.BOOL
GetVolumeInformation.errcheck: _error_check_0_is_error

GetDriveType = _kernel32.GetDriveTypeW
GetDriveType.argtypes = ctypes.wintypes.LPCWSTR,
GetDriveType.restype = ctypes.wintypes.UINT

GetDiskFreeSpaceEx = _kernel32.GetDiskFreeSpaceExW
GetDiskFreeSpaceEx.argtypes = ctypes.wintypes.LPCWSTR, ctypes.wintypes.PULARGE_INTEGER, ctypes.wintypes.PULARGE_INTEGER, ctypes.wintypes.PULARGE_INTEGER
GetDiskFreeSpaceEx.restype = ctypes.wintypes.BOOL
GetDiskFreeSpaceEx.errcheck: _error_check_0_is_error


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
    drives = []
    bitmask = GetLogicalDrives()
    for letter in string.ascii_uppercase:
        if bitmask & 1:
            drive = f'{letter}:\\'
            entry = structs.DirEntry(drive[0:-1],True,pathlib.Path(drive),None,None,None,None)
            # get name of drive
            drive_name = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH+1)
            if not GetVolumeInformation(drive,drive_name,ctypes.sizeof(drive_name),None,None,None,None,0):
                raise ctypes.WinError(ctypes.get_last_error())
            entry.extra['drive_name'] = drive_name.value
            # get drive type
            match GetDriveType(drive):
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
                    buffer_len = ctypes.wintypes.DWORD(1024)
                    buffer = ctypes.create_unicode_buffer(buffer_len.value)
                    un_buffer = ctypes.cast(buffer,LPUNIVERSAL_NAME_INFO)
                    WNetGetUniversalName(drive, UNIVERSAL_NAME_INFO_LEVEL, un_buffer, ctypes.byref(buffer_len))
                    entry.extra['remote_path'] = un_buffer[0].universal_name
                    # build name: 'share_name (net_name) (drive)'
                    path_comps = split_network_path(entry.extra['remote_path'])
                    if len(path_comps)>=2:
                        entry.name = f'{path_comps[1]} ({path_comps[0]}) ({drive[0:-1]})'
                case 5:     # DRIVE_CDROM
                    entry.mime_type = 'labManager/drive_cdrom'
                case 6:     # DRIVE_RAMDISK
                    entry.mime_type = 'labManager/drive_ramdisk'
            # get size information
            total, free = ctypes.wintypes.ULARGE_INTEGER(), ctypes.wintypes.ULARGE_INTEGER()
            GetDiskFreeSpaceEx(drive,None,ctypes.byref(total),ctypes.byref(free))
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

def get_dir_list_sync(path: pathlib.Path) -> list[structs.DirEntry]:
    # will throw when path doesn't exist or is not a directory
    path = pathlib.Path(path)
    out = []
    for e in path.iterdir():
        try:
            stat = e.stat()
            item = structs.DirEntry(e.name, e.is_dir(), pathlib.Path(e),
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


def split_network_path(path: str|pathlib.Path) -> list[str]:
    path = str(path)
    if not path.startswith(('\\\\','//')):
        return []
    # this is a network address
    # split into components
    path = path.strip('\\/').replace('\\','/')
    return [s for s in str(path).split('/') if s]

def get_net_computer(path: str|pathlib.Path):
    # determine if it is a network computer (\\SERVER) and not a path including share (\\SERVER\share)
    net_comp = split_network_path(path)
    if len(net_comp)==1:    # a single name entry, so thats just a computer
        return net_comp[0]
    return None