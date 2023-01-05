
from . import comms
from . import ifs
from . import keepalive
from . import ssdp


# import modules for master only when user has specified
# "master" extra when installing package
try:
    import authlib as al
    import httpx as h
except:
    # no-op, authlib not available means master not available
    # user didn't specify the "master" extra to have the
    # dependencies for the master installed
    pass
else:
    del al
    del h
    from . import smb
    from . import toems
