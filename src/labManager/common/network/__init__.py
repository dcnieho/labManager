import pkg_resources
from . import comms
from . import ifs
from . import keepalive
from . import ssdp
from . import wol


if 'authlib' in {pkg.key for pkg in pkg_resources.working_set}:
    from . import admin_conn
    from . import smb
    from . import toems

if 'dotenv' in {pkg.key for pkg in pkg_resources.working_set}:
    from . import ldap