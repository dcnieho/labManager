import logging
#logging.basicConfig(level=logging.DEBUG)

import impacket.smbconnection
import impacket.dcerpc.v5.srvs
import smbprotocol.open
import smbprotocol.structure

server = "srv2.humlab.lu.se"
port = 445
domain = "UW"
username = "huml-dkn"
password = "***"

access = smbprotocol.structure.FlagField(
    size=4,
    flag_type=smbprotocol.open.DirectoryAccessMask#FilePipePrinterAccessMask
)


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
    access.set_value(tree_info['MaximalAccess'])
    
    #print(share, all_shares[i]['shi1_remark'][:-1], access)
    print(share, access)
    smb_client.disconnectTree(tid)
    
