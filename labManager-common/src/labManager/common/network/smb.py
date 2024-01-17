import enum
import pathlib

# Needs aiosmb, not installed by default
from aiosmb.commons.connection.factory import SMBConnectionFactory
from aiosmb.commons.interfaces.machine import SMBMachine
from aiosmb.commons.interfaces.share import SMBShare
from aiosmb.commons.connection.target import SMBTarget
from aiosmb.wintypes.access_mask import FileAccessMask

from . import utils
from .. import structs

class AccessLevel(enum.IntFlag):
    READ = enum.auto()
    WRITE = enum.auto()
    DELETE = enum.auto()


def _check_access(flags: FileAccessMask, level: AccessLevel):
    if not flags:
        return False

    access = True
    if AccessLevel.READ in level:
        access &= (
        FileAccessMask.FILE_READ_DATA in flags and
        FileAccessMask.FILE_READ_ATTRIBUTES in flags and
        FileAccessMask.FILE_READ_EA in flags and
        FileAccessMask.READ_CONTROL in flags
        )
    if AccessLevel.WRITE in level:
        access &= (
        FileAccessMask.FILE_WRITE_DATA in flags and
        FileAccessMask.FILE_APPEND_DATA in flags and
        FileAccessMask.FILE_WRITE_ATTRIBUTES in flags and
        FileAccessMask.FILE_WRITE_EA in flags and
        FileAccessMask.READ_CONTROL in flags
        )
    if AccessLevel.DELETE in level:
        access &= (
        FileAccessMask.DELETE  in flags
        )
    return access

# convenience wrapper
async def get_shares(server: str, user: str, password: str, domain='', check_access_level: AccessLevel=None, matching='', contains=None, remove_trailing='', ignored:list[str]=['IPC$']) -> list[structs.DirEntry]:
    shares: list[structs.DirEntry] = []

    domain, user = utils.get_domain_username(user, domain)
    stage = 1
    try:
        smb_mgr = SMBConnectionFactory.from_components(server,username=user,secret=password, domain=domain, dialect='smb2')
        if isinstance(smb_mgr.credential, SMBTarget):
            # there are versions of aiosmb where these two fields were accidentally reversed, fix
            smb_mgr.target, smb_mgr.credential = smb_mgr.credential, smb_mgr.target
        async with (connection:=smb_mgr.get_connection()):
            _, err = await connection.login()
            if err is not None:
                raise err

            # prep regex for selecting shares of interest
            if matching:
                import re
                r = re.compile(matching)

            check_access = check_access_level is not None
            stage = 2
            async for share, err in SMBMachine(connection).list_shares():
                share_name: str = share.name

                # check if share is excluded
                if share_name in ignored:
                    continue

                # check if share name contains expected string
                if contains and contains not in share_name:
                    continue

                # check if share name matches format we're interested in
                if matching:
                    if not r.match(share_name):
                        continue

                # remove trailing stuff from share name
                if remove_trailing:
                    if share_name.endswith(remove_trailing):
                        share_name = share_name[:-len(remove_trailing)]

                if check_access:
                    _, err = await share.connect(connection)     # connect so the maximal_access field gets filled
                    if err is not None:
                        continue    # no access at all
                    if share.tree_id is not None:
                        await connection.tree_disconnect(share.tree_id)
                    # check if we have access at requested level
                    if not _check_access(share.maximal_access, check_access_level):
                        continue

                # NB: //SERVER/ is the format pathlib understands and can concatenate share names to
                shares.append(structs.DirEntry(share_name,True,pathlib.Path(f'//{server}/') / share_name,None,None,None,'labManager/net_share'))
    except Exception as exc:
        if stage==1:
            raise RuntimeError(f'The system cannot find the specified network computer \\\\{server}, or cannot connect using the provided credentials (domain "{domain}", user "{user}"): {exc}')
        elif stage==2:
            raise RuntimeError(f'Error listing shares on server {server} when connected using domain "{domain}", user "{user}": {exc}')

    return shares

async def check_share(server: str, user: str, password: str, share_name: str, domain='', check_access_level: AccessLevel=None):
    domain, user = utils.get_domain_username(user, domain)
    stage = 1
    try:
        smb_mgr = SMBConnectionFactory.from_components(server,username=user,secret=password, domain=domain, dialect='smb2')
        if isinstance(smb_mgr.credential, SMBTarget):
            # there are versions of aiosmb where these two fields were accidentally reversed, fix
            smb_mgr.target, smb_mgr.credential = smb_mgr.credential, smb_mgr.target
        async with (connection:=smb_mgr.get_connection()):
            _, err = await connection.login()
            if err is not None:
                raise err

            # now try to connect to the share
            share = SMBShare(fullpath = '\\\\%s\\%s' % (connection.target.get_hostname_or_ip(), share_name))
            _, err = await share.connect(connection)     # connect so the maximal_access field gets filled
            if err is not None:
                return False    # no access at all
            if share.tree_id is not None:
                await connection.tree_disconnect(share.tree_id)

            # check if we have access at requested level
            return _check_access(share.maximal_access, check_access_level)

    except Exception as exc:
        if stage==1:
            raise RuntimeError(f'The system cannot find the specified network computer \\\\{server}, or cannot connect using the provided credentials (domain "{domain}", user "{user}"): {exc}')
        elif stage==2:
            raise RuntimeError(f'Error connecting to share "{share_name}" on server {server} when connected using domain "{domain}", user "{user}": {exc}')

    return False