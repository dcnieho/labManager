import logging
#logging.basicConfig(level=logging.DEBUG)

import smbprotocol.open
import smbprotocol.structure

import sys
import pathlib
src_path = str(pathlib.Path(__file__).parent/"src")
if not src_path in sys.path:
    sys.path.append(src_path)
    
import labManager.utils.impacket.smbconnection  as smbconnection
import labManager.utils.impacket.dcerpc.v5.srvs as dcerpc_v5_srvs
import labManager.utils.impacket.smb3structs    as smb3structs

server   = "srv2.humlab.lu.se"
port     = 445
domain   = "UW"
username = "huml-dkn"
password = ""

access_flags = smbprotocol.structure.FlagField(
    size=4,
    flag_type=smbprotocol.open.FilePipePrinterAccessMask
)

def check_access(access_flags: smbprotocol.structure.FlagField):
    flags = access_flags.get_value()
    return bool(
        # these flags are a bit arbitrary, but this seems like pretty complete access, good enough
        (flags & smb3structs.DELETE) and
        (flags & smb3structs.FILE_READ_DATA) and
        (flags & smb3structs.FILE_WRITE_DATA) and
        (flags & smb3structs.FILE_EXECUTE)
        )


if not password:
    from getpass import getpass
    password = getpass(f'Password for {domain}\{username}: ')


# get all shares on the server
smb_client = smbconnection.SMBConnection(server, server, sess_port=port)
smb_client.login(username, password, domain)
all_shares = smb_client.listShares()
for i in range(len(all_shares)):
    share = all_shares[i]['shi1_netname'][:-1]  # remove NULL string terminator
    if all_shares[i]['shi1_type'] & dcerpc_v5_srvs.STYPE_SPECIAL:  # skip administrative shares such as ADMIN$, IPC$, C$, etc
        continue
    tid = smb_client.connectTree(share)
    tree_info = smb_client._SMBConnection._Session['TreeConnectTable'][share]
    access_flags.set_value(tree_info['MaximalAccess'])
    smb_client.disconnectTree(tid)
    
    have_access = check_access(access_flags)
    print(f'{share}: {have_access} ({access_flags})')
    
