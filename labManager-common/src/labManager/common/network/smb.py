import enum

from aiosmb.commons.connection.factory import SMBConnectionFactory
from aiosmb.commons.interfaces.machine import SMBMachine
from aiosmb.commons.interfaces.share import SMBShare
from aiosmb.commons.connection.target import SMBTarget
from aiosmb.wintypes.access_mask import FileAccessMask

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
async def get_shares(server: str, user: str, password: str, domain='', check_access_level: AccessLevel=None, matching='', contains=None, remove_trailing='', ignored:list[str]=['IPC$']):
    shares = []

    domain, user = get_domain_username(user, domain)
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
                share_name = share.name

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
                    if _check_access(share.maximal_access, check_access_level):
                        shares.append(share_name)
                else:
                    shares.append(share_name)
    except Exception as exc:
        if stage==1:
            print(f'SMB: Error connecting using domain "{domain}", user "{user}" to {server}: {exc}')
        elif stage==2:
            print(f'SMB: Error listing shares on server using {server} when connected using domain "{domain}", user "{user}": {exc}')

    return shares

async def check_share(server: str, user: str, password: str, share_name: str, domain='', check_access_level: AccessLevel=None):
    domain, user = get_domain_username(user, domain)
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
            print(f'SMB: Error connecting using domain "{domain}", user "{user}" to {server}: {exc}')
        elif stage==2:
            print(f'SMB: Error connecting to share "{share_name}" on server using {server} when connected using domain "{domain}", user "{user}": {exc}')

    return False


def get_domain_username(user: str, default_domain: str):
    # figure out domain from user (format domain\user)
    # if no domain found in user string, use default_domain
    domain = default_domain
    if '\\' in user:
        dom, user = user.split('\\', maxsplit=1)
        if dom:
            domain = dom
    return domain, user