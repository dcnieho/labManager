import enum

from aiosmb.commons.connection.factory import SMBConnectionFactory
from aiosmb.commons.interfaces.machine import SMBMachine
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
async def get_shares(server: str, user: str, password: str, domain='', check_access_level=None, matching='', contains=None, remove_trailing=''):
    shares = []

    domain, user = get_domain_username(user, domain)
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
            async for share, err in SMBMachine(connection).list_shares(check_access):
                # check if share name matches format we're interested in
                share_name = share.name
                if matching:
                    if not r.match(share_name):
                        continue

                if contains and contains not in share_name:
                    continue

                # remove trailing stuff from share name
                if remove_trailing:
                    if share_name.endswith(remove_trailing):
                        share_name = share_name[:-len(remove_trailing)]

                if check_access:
                    # check if we have access
                    if _check_access(share.maximal_access, check_access_level):
                        shares.append(share_name)
                else:
                    shares.append(share_name)
    except Exception as exc:
        print(f'SMB: Error connecting using domain "{domain}", user "{user}" to {server}: {exc}')

    return shares

def get_domain_username(user: str, default_domain: str):
    # figure out domain from user (format domain\user)
    # if no domain found in user string, use default_domain
    domain = default_domain
    if '\\' in user:
        dom, user = user.split('\\', maxsplit=1)
        if dom:
            domain = dom
    return domain, user