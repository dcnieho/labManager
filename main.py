import logging
#logging.basicConfig(level=logging.DEBUG)

import impacket.smbconnection
import impacket.dcerpc.v5.srvs
import impacket.smb3structs
import smbprotocol.open
import smbprotocol.structure

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
        (flags & impacket.smb3structs.DELETE) and
        (flags & impacket.smb3structs.FILE_READ_DATA) and
        (flags & impacket.smb3structs.FILE_WRITE_DATA) and
        (flags & impacket.smb3structs.FILE_EXECUTE)
        )


if not password:
    from getpass import getpass
    password = getpass(f'Password for {domain}\{username}: ')


# get all shares on the server
smb_client = impacket.smbconnection.SMBConnection(server, server, sess_port=port)
smb_client.login(username, password, domain)
all_shares = smb_client.listShares()
for i in range(len(all_shares)):
    share = all_shares[i]['shi1_netname'][:-1]  # remove NULL string terminator
    if all_shares[i]['shi1_type'] & impacket.dcerpc.v5.srvs.STYPE_SPECIAL:  # skip administrative shares such as ADMIN$, IPC$, C$, etc
        continue
    tid = smb_client.connectTree(share)
    tree_info = smb_client._SMBConnection._Session['TreeConnectTable'][share]
    access_flags.set_value(tree_info['MaximalAccess'])
    smb_client.disconnectTree(tid)
    
    have_access = check_access(access_flags)
    print(f'{share}: {have_access} ({access_flags})')
    
