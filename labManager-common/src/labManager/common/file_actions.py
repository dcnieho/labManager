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
_netapi32 = ctypes.WinDLL('netapi32',use_last_error=True)
_shell32 = ctypes.WinDLL('shell32',use_last_error=True)


ERROR_NO_MORE_ITEMS = 259
ERROR_EXTENDED_ERROR= 1208
ERROR_SESSION_CREDENTIAL_CONFLICT = 1219

def _error_check_non0_is_error_ex(allowed, result, func, args):
    if not result or result in allowed:
        return result
    if result==ERROR_EXTENDED_ERROR:
        last_error = ctypes.wintypes.DWORD()
        provider = ctypes.create_unicode_buffer(256)
        description = ctypes.create_unicode_buffer(256)
        WNetGetLastError(ctypes.byref(last_error),description,ctypes.sizeof(description),provider,ctypes.sizeof(provider))
        raise ctypes.WinError(last_error.value, f'{provider.value} failed: {description.value}')
    if (err := ctypes.get_last_error()):
        result = err
    raise ctypes.WinError(result)
def _error_check_non0_is_error(result, func, args):
    return _error_check_non0_is_error_ex([], result, func, args)
def _error_check_0_is_error(result, func, args):
    if not result:
        raise ctypes.WinError(ctypes.get_last_error())

SHGFP_TYPE_CURRENT  = 0     # Get current, not default value
CSIDL_DESKTOP       = 0
CSIDL_PROFILE       = 40
CSIDL_MYDOCUMENTS   = 5     # AKA CSIDL_PERSONAL
SHGetFolderPath = _shell32.SHGetFolderPathW
SHGetFolderPath.argtypes = ctypes.wintypes.HWND, ctypes.c_int, ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD, ctypes.wintypes.LPWSTR
SHGetFolderPath.restype = ctypes.wintypes.DWORD
SHGetFolderPath.errcheck = _error_check_non0_is_error

class NETRESOURCE(ctypes.Structure):
	_fields_ = [
		('scope', ctypes.wintypes.DWORD),
		('type', ctypes.wintypes.DWORD),
		('display_type', ctypes.wintypes.DWORD),
		('usage', ctypes.wintypes.DWORD),
		('local_name', ctypes.wintypes.LPWSTR),
		('remote_name', ctypes.wintypes.LPWSTR),
		('comment', ctypes.wintypes.LPWSTR),
		('provider', ctypes.wintypes.LPWSTR),
	]
LPNETRESOURCE = ctypes.POINTER(NETRESOURCE)

RESOURCE_CONNECTED  = 0x00000001
RESOURCE_GLOBALNET  = 0x00000002
RESOURCE_REMEMBERED = 0x00000003
RESOURCE_RECENT     = 0x00000004
RESOURCE_CONTEXT    = 0x00000005
RESOURCETYPE_ANY        = 0x00000000
RESOURCETYPE_DISK       = 0x00000001
RESOURCETYPE_PRINT      = 0x00000002
RESOURCETYPE_RESERVED   = 0x00000008
RESOURCETYPE_UNKNOWN    = 0xFFFFFFFF
RESOURCEUSAGE_CONNECTABLE   = 0x00000001
RESOURCEUSAGE_CONTAINER     = 0x00000002
RESOURCEUSAGE_NOLOCALDEVICE = 0x00000004
RESOURCEUSAGE_SIBLING       = 0x00000008
RESOURCEUSAGE_ATTACHED      = 0x00000010
RESOURCEDISPLAYTYPE_GENERIC         = 0x00000000
RESOURCEDISPLAYTYPE_DOMAIN          = 0x00000001
RESOURCEDISPLAYTYPE_SERVER          = 0x00000002
RESOURCEDISPLAYTYPE_SHARE           = 0x00000003
RESOURCEDISPLAYTYPE_FILE            = 0x00000004
RESOURCEDISPLAYTYPE_GROUP           = 0x00000005
RESOURCEDISPLAYTYPE_NETWORK         = 0x00000006
RESOURCEDISPLAYTYPE_ROOT            = 0x00000007
RESOURCEDISPLAYTYPE_SHAREADMIN      = 0x00000008
RESOURCEDISPLAYTYPE_DIRECTORY       = 0x00000009
RESOURCEDISPLAYTYPE_TREE            = 0x0000000A
RESOURCEDISPLAYTYPE_NDSCONTAINER    = 0x0000000B

WNetGetLastError = _mpr.WNetGetLastErrorW
WNetGetLastError.argtypes = ctypes.wintypes.LPDWORD, ctypes.wintypes.LPWSTR, ctypes.wintypes.DWORD, ctypes.wintypes.LPWSTR, ctypes.wintypes.DWORD
WNetGetLastError.restype = ctypes.wintypes.DWORD
WNetGetLastError.errcheck = _error_check_non0_is_error

WNetOpenEnum = _mpr.WNetOpenEnumW
WNetOpenEnum.argtypes = ctypes.wintypes.DWORD, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD, LPNETRESOURCE, ctypes.wintypes.LPHANDLE
WNetOpenEnum.restype = ctypes.wintypes.DWORD
WNetOpenEnum.errcheck = _error_check_non0_is_error

WNetEnumResource = _mpr.WNetEnumResourceW
WNetEnumResource.argtypes = ctypes.wintypes.HANDLE, ctypes.wintypes.LPDWORD, ctypes.wintypes.LPVOID, ctypes.wintypes.LPDWORD
WNetEnumResource.restype = ctypes.wintypes.DWORD
WNetEnumResource.errcheck = lambda r,f,a: _error_check_non0_is_error_ex([ERROR_NO_MORE_ITEMS], r, f, a)

CONNECT_TEMPORARY = 0x00000004
WNetAddConnection2 = _mpr.WNetAddConnection2W
WNetAddConnection2.argtypes = LPNETRESOURCE, ctypes.wintypes.LPCWSTR, ctypes.wintypes.LPCWSTR, ctypes.wintypes.DWORD
WNetAddConnection2.restype = ctypes.wintypes.DWORD
WNetAddConnection2.errcheck = lambda r,f,a: _error_check_non0_is_error_ex([ERROR_SESSION_CREDENTIAL_CONFLICT], r, f, a)

WNetGetUser = _mpr.WNetGetUserW
WNetGetUser.argtypes = ctypes.wintypes.LPCWSTR, ctypes.wintypes.LPWSTR, ctypes.wintypes.LPDWORD
WNetGetUser.restype = ctypes.wintypes.DWORD
WNetGetUser.errcheck = _error_check_non0_is_error

WNetCancelConnection2 = _mpr.WNetCancelConnection2W
WNetCancelConnection2.argtypes = ctypes.wintypes.LPCWSTR, ctypes.wintypes.DWORD, ctypes.wintypes.BOOL
WNetCancelConnection2.restype = ctypes.wintypes.DWORD
WNetCancelConnection2.errcheck = _error_check_non0_is_error

WNetCloseEnum = _mpr.WNetCloseEnum
WNetCloseEnum.argtypes = ctypes.wintypes.HANDLE,
WNetCloseEnum.restype = ctypes.wintypes.DWORD
WNetCloseEnum.errcheck = _error_check_non0_is_error


GMEM_FIXED    = 0x0000
GMEM_MOVEABLE = 0x0002
GMEM_ZEROINIT = 0x0040
GHND          = GMEM_ZEROINIT | GMEM_MOVEABLE
GPTR          = GMEM_ZEROINIT | GMEM_FIXED
GlobalAlloc = _kernel32.GlobalAlloc
GlobalAlloc.argtypes = ctypes.wintypes.UINT, ctypes.c_ssize_t
GlobalAlloc.restype = ctypes.wintypes.HANDLE
GlobalFree = _kernel32.GlobalFree
GlobalFree.argtypes = ctypes.wintypes.HANDLE,
GlobalFree.restype = ctypes.wintypes.HANDLE
GlobalFree.errcheck = _error_check_non0_is_error


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


LMCSTR = ctypes.wintypes.LPCWSTR
LMSTR = ctypes.wintypes.LPWSTR
NET_API_STATUS = ctypes.wintypes.DWORD
NERR_Success = 0
NERR_NetNameNotFound = 2100+210
MAX_PREFERRED_LENGTH = ctypes.wintypes.DWORD(-1)

class SHARE_INFO_0(ctypes.Structure):
	_fields_ = [
		('netname', LMSTR),
	]
LPSHARE_INFO_0 = ctypes.POINTER(SHARE_INFO_0)
class SHARE_INFO_1(ctypes.Structure):
	_fields_ = [
		('netname', LMSTR),
		('type', ctypes.wintypes.DWORD),
		('remark', LMSTR),
	]
LPSHARE_INFO_1 = ctypes.POINTER(SHARE_INFO_1)

# SHARE_INFO_1.type flags
STYPE_DISKTREE          = 0
STYPE_PRINTQ            = 1
STYPE_DEVICE            = 2
STYPE_IPC               = 3
STYPE_TEMPORARY         = 0x40000000
STYPE_SPECIAL           = 0x80000000

NetShareEnum = _netapi32.NetShareEnum
NetShareEnum.argtypes = (LMSTR, ctypes.wintypes.DWORD, ctypes.POINTER(ctypes.wintypes.LPBYTE), ctypes.wintypes.DWORD, ctypes.wintypes.LPDWORD, ctypes.wintypes.LPDWORD, ctypes.wintypes.LPDWORD)
NetShareEnum.restype = NET_API_STATUS
NetShareEnum.errcheck = _error_check_non0_is_error

NetShareGetInfo = _netapi32.NetShareGetInfo
NetShareGetInfo.argtypes = (LMSTR, LMSTR, ctypes.wintypes.DWORD, ctypes.POINTER(ctypes.wintypes.LPBYTE))
NetShareGetInfo.restype = NET_API_STATUS
NetShareGetInfo.errcheck = lambda r,f,a: _error_check_non0_is_error_ex([NERR_NetNameNotFound], r, f, a)

NetApiBufferFree = _netapi32.NetApiBufferFree
NetApiBufferFree.argtypes = (ctypes.wintypes.LPVOID,)
NetApiBufferFree.restype = NET_API_STATUS


def get_thispc_listing() -> list[structs.DirEntry]:
    items = []
    # NB: we also check for Downloads folder (doesn't have a CSIDL), add if found
    # expect it as a subfolder of user's profile folder
    for d, subdir, disp_name in [(CSIDL_DESKTOP, None, 'Desktop'), (CSIDL_MYDOCUMENTS, None, 'My Documents'), (CSIDL_PROFILE, 'Downloads', 'Downloads')]:
        buf = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
        SHGetFolderPath(None, d, None, SHGFP_TYPE_CURRENT, buf)
        path = pathlib.Path(buf.value)
        if subdir:
            path /= subdir
            if not path.is_dir():
                continue
        stat = path.stat()
        item = structs.DirEntry(disp_name, path.is_dir(), path,
                                stat.st_ctime, stat.st_mtime, stat.st_size,
                                mimetypes.guess_type(path)[0])
        items.append(item)
    return items


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

def get_visible_shares(server: str, user: str='', password: str='', domain=''):
    shares: list[structs.DirEntry] = []

    # if no user provided, user should be able to connect from the Windows
    # explorer to this server (e.g. user should have provided credentials)
    # if you can open it in explorer, you can list it using this call)
    if user:
        need_conn_cleanup = _server_login(server, user, password, domain)
    else:
        need_conn_cleanup = False

    server = server.strip('\\/')
    nr = NETRESOURCE(remote_name=f'\\\\{server}')

    hEnum = ctypes.wintypes.HANDLE()
    WNetOpenEnum(RESOURCE_GLOBALNET, RESOURCETYPE_ANY, 0, nr, ctypes.byref(hEnum))

    # allocate a buffer
    cbBuffer = ctypes.wintypes.DWORD(16384)     # 16K is a good size
    mem_handle = GlobalAlloc(GPTR, cbBuffer.value)
    lpnrLocal = ctypes.cast(mem_handle,LPNETRESOURCE)

    while True:
        cEntries = ctypes.wintypes.DWORD(-1)        # enumerate all possible entries
        result = WNetEnumResource(hEnum, ctypes.byref(cEntries),lpnrLocal,ctypes.byref(cbBuffer))
        if result==ERROR_NO_MORE_ITEMS:
            break

        for idx in range(cEntries.value):
            share = lpnrLocal[idx]
            share_path = share.remote_name
            path_comps = split_network_path(share_path)
            if len(path_comps)>=2:
                shares.append(structs.DirEntry(path_comps[1],True,pathlib.Path(share_path),None,None,None,'labManager/net_share'))

    # clean up
    GlobalFree(mem_handle)
    WNetCloseEnum(hEnum)
    if need_conn_cleanup:
        _server_logout(server)

    return shares

def get_all_shares(server: str, user: str='', password: str='', domain=''):
    # similar to get_visible_shares() but also lists hidden shares
    shares: list[structs.DirEntry] = []

    # if no user provided, user should be able to connect from the Windows
    # explorer to this server (e.g. user should have provided credentials)
    # if you can open it in explorer, you can list it using this call)
    if user:
        need_conn_cleanup = _server_login(server, user, password, domain)
    else:
        need_conn_cleanup = False

    server = server.strip('\\/')
    buf = ctypes.wintypes.LPBYTE()
    buf_len = MAX_PREFERRED_LENGTH
    read, total = ctypes.wintypes.DWORD(0), ctypes.wintypes.DWORD(0)
    NetShareEnum(f'\\\\{server}', 1, ctypes.byref(buf), buf_len, ctypes.byref(read), ctypes.byref(total), None)

    UserInfoArray = SHARE_INFO_1 * read.value
    remote_shares = UserInfoArray.from_address(ctypes.addressof(buf.contents))
    for share in remote_shares:
        shares.append(structs.DirEntry(share.netname,True,pathlib.Path(f'\\\\{server}\\{share.netname}'),None,None,None,'labManager/net_share'))

    # clean up
    NetApiBufferFree(buf)
    if need_conn_cleanup:
        _server_logout(server)

    return shares

def check_share(server: str, share: str, user: str='', password: str='', domain=''):
    # if no user provided, user should be able to connect from the Windows
    # explorer to this server (e.g. user should have provided credentials)
    # if you can open it in explorer, you can list it using this call)
    if user:
        need_conn_cleanup = _server_login(server, user, password, domain)
    else:
        need_conn_cleanup = False

    server = server.strip('\\/')
    buf = ctypes.wintypes.LPBYTE()
    ret = NetShareGetInfo(server,share,0,buf)   # NB: I never got NetShareCheck to work, always return that share doesn't exist, so using this slightly round-about way (we can't get info of a non-existent share)
    if ret==NERR_Success:
        NetApiBufferFree(buf)

    if need_conn_cleanup:
        _server_logout(server)

    return ret!=NERR_NetNameNotFound

def _server_login(server: str, user: str, password: str, domain=''):
    # check if we already have a connection with the server, and if so, if
    # it is the expected user
    user_name = ctypes.create_unicode_buffer(1024)
    buf_size = ctypes.wintypes.DWORD(ctypes.sizeof(user_name))
    server = server.strip('\\/')
    server = f'\\\\{server}'
    try:
        WNetGetUser(server, user_name, ctypes.byref(buf_size))
    except:
        need_conn = True
    else:
        user_name = user_name.value
        need_conn = user_name!=user and not user_name.endswith(user)
    if need_conn:
        nr = NETRESOURCE(type=RESOURCETYPE_DISK, remote_name=server)
        if domain:
            user = f'{domain}\\{user}'
        res = WNetAddConnection2(nr, password, user, CONNECT_TEMPORARY)
        if res==ERROR_SESSION_CREDENTIAL_CONFLICT:
            # apparently we are connected even if WNetGetUser() doesn't know. Carry on and hope for the best
            need_conn = False
        elif res!=0:
            _error_check_non0_is_error(res,None,None)
    return need_conn
def _server_logout(server: str):
    server = server.strip('\\/')
    server = f'\\\\{server}'
    try:
        WNetCancelConnection2(server, 0, True)
    except:
        # ok if this fails, we did our best
        pass

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

async def make_dir(path: str | pathlib.Path, exist_ok: bool = False):
    pathvalidate.validate_filepath(path, "auto")
    path = aiopath.AsyncPath(path)
    await path.mkdir(exist_ok=exist_ok)

async def make_file(path: str | pathlib.Path, exist_ok: bool = True):
    pathvalidate.validate_filepath(path, "auto")
    path = aiopath.AsyncPath(path)
    await path.touch(exist_ok=exist_ok)

async def rename_path(old_path: str | pathlib.Path, new_path: str | pathlib.Path):
    pathvalidate.validate_filepath(old_path, "auto")
    pathvalidate.validate_filepath(new_path, "auto")
    return await aiopath.AsyncPath(old_path).rename(new_path)

async def copy_path(source_path: str | pathlib.Path, dest_path: str | pathlib.Path, dirs_exist_ok: bool = False):
    pathvalidate.validate_filepath(source_path, "auto")
    pathvalidate.validate_filepath(dest_path, "auto")
    source_path = aiopath.AsyncPath(source_path)
    dest_path   = aiopath.AsyncPath(dest_path)
    if await source_path.is_dir():
        return await aioshutil.copytree(source_path, dest_path, dirs_exist_ok=dirs_exist_ok)
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
        await aioshutil.rmtree(path)
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


# some functions that could be useful for debug

def _print_share_info_1(share: SHARE_INFO_1):
    # for debug
    print(f"\tName:     {share.netname}")
    print(f"\tType:     {share.type}, ", end='')
    if (share.type & STYPE_DISKTREE) == STYPE_DISKTREE:
        print('disk tree ', end='')
    elif (share.type & STYPE_PRINTQ) == STYPE_PRINTQ:
        print('print queue ', end='')
    elif (share.type & STYPE_DEVICE) == STYPE_DEVICE:
        print('device ', end='')
    elif (share.type & STYPE_IPC) == STYPE_IPC:
        print('ipc ', end='')
    if (share.type & STYPE_TEMPORARY) == STYPE_TEMPORARY:
        print("(temporary)")
    elif (share.type & STYPE_SPECIAL) == STYPE_SPECIAL:
        print("(special)")
    else:
        print('')
    print(f"\tComment:  {share.remark}\n")

def _print_netresource(i: str, lpnr: NETRESOURCE):
    print(f"NETRESOURCE[{i}] Scope: ", end='')
    if lpnr.scope==RESOURCE_CONNECTED:
        print("connected")
    elif lpnr.scope==RESOURCE_GLOBALNET:
        print("all resources")
    elif lpnr.scope==RESOURCE_REMEMBERED:
        print("remembered")
    else:
        print(f"unknown scope {lpnr.scope}")

    print(f"NETRESOURCE[{i}] Type: ", end='')
    if lpnr.type==RESOURCETYPE_ANY:
        print("any")
    elif lpnr.type==RESOURCETYPE_DISK:
        print("disk")
    elif lpnr.type==RESOURCETYPE_PRINT:
        print("print")
    else:
        print(f"unknown type {lpnr.type}")

    print(f"NETRESOURCE[{i}] DisplayType: ", end='')
    if lpnr.display_type==RESOURCEDISPLAYTYPE_GENERIC:
        print("generic")
    elif lpnr.display_type==RESOURCEDISPLAYTYPE_DOMAIN:
        print("domain")
    elif lpnr.display_type==RESOURCEDISPLAYTYPE_SERVER:
        print("server")
    elif lpnr.display_type==RESOURCEDISPLAYTYPE_SHARE:
        print("share")
    elif lpnr.display_type==RESOURCEDISPLAYTYPE_FILE:
        print("file")
    elif lpnr.display_type==RESOURCEDISPLAYTYPE_GROUP:
        print("group")
    elif lpnr.display_type==RESOURCEDISPLAYTYPE_NETWORK:
        print("network")
    elif lpnr.display_type==RESOURCEDISPLAYTYPE_ROOT:
        print("root")
    elif lpnr.display_type==RESOURCEDISPLAYTYPE_SHAREADMIN:
        print("shareadmin")
    elif lpnr.display_type==RESOURCEDISPLAYTYPE_DIRECTORY:
        print("directory")
    elif lpnr.display_type==RESOURCEDISPLAYTYPE_TREE:
        print("tree")
    elif lpnr.display_type==RESOURCEDISPLAYTYPE_NDSCONTAINER:
        print("NDS container")
    else:
        print(f"unknown display type {lpnr.display_type}")

    print(f"NETRESOURCE[{i}] Usage: 0x{lpnr.usage:x} = ", end='')
    if (lpnr.usage & RESOURCEUSAGE_CONNECTABLE) == RESOURCEUSAGE_CONNECTABLE:
        print("connectable ", end='')
    if (lpnr.usage & RESOURCEUSAGE_CONTAINER) == RESOURCEUSAGE_CONTAINER:
        print("container ", end='')
    print("")

    print(f"NETRESOURCE[{i}] Localname: {lpnr.local_name}")
    print(f"NETRESOURCE[{i}] Remotename: {lpnr.remote_name}")
    print(f"NETRESOURCE[{i}] Comment: {lpnr.comment}")
    print(f"NETRESOURCE[{i}] Provider: {lpnr.provider}")
    print("")