import pkg_resources

from . import common
from . import client
if 'authlib' in {pkg.key for pkg in pkg_resources.working_set}:
    from . import master

from .version import __version__, __url__, __author__, __email__, __description__