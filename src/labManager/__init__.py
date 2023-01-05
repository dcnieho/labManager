from . import client
from . import utils
from .version import __version__, __url__, __author__, __email__, __description__

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
    from . import master