import logging
from xmlrpc.client import Boolean
#logging.basicConfig(level=logging.DEBUG)

import impacket.smbconnection
import smbprotocol.exceptions
import smbclient

server = "srv2.humlab.lu.se"
port = 445
domain = "UW"
username = "huml-dkn"
password = "***"

# get all shares on the server
smb_client = impacket.smbconnection.SMBConnection(server, server, sess_port=port)
smb_client.login(username, password, domain)
all_shares = smb_client.listShares()
del smb_client

# see which of these we can connect to
for i in range(len(all_shares)):
    try:
        access = True
        share = r"\\%s\%s" % (server,all_shares[i]['shi1_netname'][:-1])
        s = smbclient.listdir(share)
    except (smbprotocol.exceptions.SMBOSError, smbprotocol.exceptions.SMBResponseException) as err:
        # bit of extra logic here because trying to list ADMIN$ or IPC$ yield different error than normal
        # inaccessible shares
        if hasattr(err,'message'):
            errstr = err.message
        else:
            errstr = err.strerror
        if 'STATUS_ACCESS_DENIED' in errstr or 'STATUS_INVALID_PARAMETER' in errstr:
            access = False
        else:
            raise err
        
    print(all_shares[i]['shi1_netname'][:-1],all_shares[i]['shi1_type'],all_shares[i]['shi1_remark'][:-1], access)
