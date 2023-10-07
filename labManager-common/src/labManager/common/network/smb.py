from ..impacket import smbconnection  as smbconnection
from ..impacket.dcerpc.v5 import srvs as dcerpc_v5_srvs
from ..impacket import smb3structs    as smb3structs
# provide labManager.common.network.smb.SessionError:
from ..impacket.smbconnection import SessionError as SessionError


def _check_access(flags: int):
    # flags is a FilePipePrinterAccessMask
    return bool(
        # these flags are a bit arbitrary, but this seems like pretty complete access, good enough
        (flags & smb3structs.DELETE) and
        (flags & smb3structs.FILE_READ_DATA) and
        (flags & smb3structs.FILE_WRITE_DATA) and
        (flags & smb3structs.FILE_EXECUTE)
    )

class SMBHandler:
    def __init__(self, server, username, domain, password):
        self.server = server
        self.username = username
        self.domain = domain
        # NB: don't store password

        self.smb_client = smbconnection.SMBConnection(remoteName='*SMBSERVER', remoteHost=self.server)
        self.smb_client.login(self.username, password, self.domain)

    def list_shares(self, check_access=True, matching='', remove_trailing='', contains=None):
        # get all shares on the server
        all_shares = self.smb_client.listShares()

        # prep regex for selecting shares of interest
        if matching:
            import re
            r = re.compile(matching)

        out = []
        for i in range(len(all_shares)):
            share = all_shares[i]['shi1_netname'][:-1]  # remove NULL string terminator
            if all_shares[i]['shi1_type'] & dcerpc_v5_srvs.STYPE_SPECIAL:
                # skip administrative shares such as ADMIN$, IPC$, C$, etc
                continue

            # check if share name matches format we're interested in
            if matching:
                if not r.match(share):
                    continue

            if contains and contains not in share:
                continue

            if check_access:
                # connect to the share so we can read the user's access rights
                tid = self.smb_client.connectTree(share)
                access_flags = self.smb_client._SMBConnection._Session['TreeConnectTable'][share]['MaximalAccess']
                self.smb_client.disconnectTree(tid)

                # check if we have access
                if _check_access(access_flags):
                    out.append(share)
            else:
                out.append(share)

            # remove trailing stuff from share name
            if remove_trailing:
                for i,s in enumerate(out):
                    if s.endswith(remove_trailing):
                        out[i] = out[i][:-len(remove_trailing)]

        return out

    def close(self):
        if self.smb_client:
            self.smb_client.close()

    def __del__(self):
        self.close()