import strictyaml as s
import pathlib

from . import task

_master_schema = s.Map({
# tag::master_schema[]
    'network': s.Str(),                                     # Network on which to discover clients, e.g. 10.0.1.0/24
    'service_discovery_protocol':                           # Protocol to use for client discovery, MDNS or SSDP
        s.Enum(['MDNS','SSDP']),
    s.Optional('MDNS'): s.Map({
        'service': s.Str(),                                 # service name to discover when using MDNS, e.g., _master._labManager._tcp.local.
    }),
    s.Optional('SSDP'): s.Map({
        'device_type': s.Str(),                             # device type to announce and listen for when using SSDP, e.g., urn:schemas-upnp-org:device:labManager
    }),
    s.Optional('projects'): s.Map({                         # table of alias names for projects (to enable showing more friendly names)
        'name_table': s.MapPattern(s.Str(), s.Str()),       # example entry: `0000-01: Demo environment`
    }),
    s.Optional('base_image_name_table'):                    # table of alias names for disk images (to enable showing more friendly names)
        s.MapPattern(s.Str(), s.Str()),                     # example entry: `station_base: Windows station`
    s.Optional('SMB'): s.Map({
        'server': s.Str(),
        'domain': s.Str(),
        'projects': s.Map({
            'format': s.Str(),
            s.Optional('remove_trailing', default=''):
                s.Str(),
        }),
        'mount_share_on_client': s.Bool(),
        'mount_drive_letter': s.Str(),
        'mount_only_known_clients': s.Bool(),
    }),
    s.Optional('admin'): s.Map({
        'server': s.Str(),
        'port': s.Int(),
    }),
    s.Optional('toems'): s.Map({
        'server': s.Str(),
        'port': s.Int(),
        'images': s.Map({
            'format': s.Str(),
        }),
        s.Optional('pre_upload_script'): s.Str(),
        s.Optional('image_info_script'): s.Str(),
        s.Optional('image_info_script_partition'):
            s.Int(),
    }),
    s.Optional('login'): s.Map({
        'hint': s.Str(),
    }),
    s.Optional('clients'): s.Seq(
        s.Map({
            'name': s.Str(),
            'MAC' : s.CommaSeparated(s.Str()),
        })
    ),
    s.Optional('tasks'): s.Seq(
        s.Map({
            'name': s.Str(),
            'type': s.Enum(task.types),
            s.Optional('payload', default=''): s.Str(),
            s.Optional('payload_type', default='text'):
                s.Enum(['text','file']),
            s.Optional('cwd', default=''): s.Str(),
            s.Optional('env', default=''): s.Str(),
            s.Optional('interactive', default=False):
                s.Bool(),
            s.Optional('python_unbuffered', default=False):
                s.Bool(),
        }),
    ),
# end::master_schema[]
})
_default_master_config_file = 'master.yaml'
master = None

_client_schema = s.Map({
# tag::client_schema[]
    'network': s.Str(),
    'network_retry': s.Map({
        'number_tries': s.Int(),
        'wait': s.Int(),
    }),
    'service_discovery_protocol': s.Str(),
    s.Optional('MDNS'): s.Map({
        'service': s.Str(),
    }),
    s.Optional('SSDP'): s.Map({
        'device_type': s.Str(),
    }),
# end::client_schema[]
})
_default_client_config_file = 'client.yaml'
client = None

_admin_server_schema = s.Map({
# tag::admin-server_schema[]
    'LDAP': s.Map({
        'server': s.Str(),
        'projects': s.Map({
            'format': s.Str(),
        }),
    }),
    'toems': s.Map({
        'server': s.Str(),
        'port': s.Int(),
        'images': s.Map({
            'format': s.Str(),
            'file_copy_actions': s.Seq(
                s.Map({
                    'name': s.Str(),
                    'partition_id': s.Int(),
                }),
            ),
            'standard': s.Seq(
                s.Str(),
            ),
        }),
    }),
# end::admin-server_schema[]
})
_default_admin_server_config_file = 'admin_server.yaml'
admin_server = None

def load(which: str, file: str|pathlib.Path = None):
    global master, client, admin_server

    match which:
        case 'master':
            schema = _master_schema
            default_file = _default_master_config_file
        case 'client':
            schema = _client_schema
            default_file = _default_client_config_file
        case 'admin_server':
            schema = _admin_server_schema
            default_file = _default_admin_server_config_file
        case _:
            raise ValueError(f"which input argument '{which}' not recognized, should be one of 'master', 'client', 'admin_server'")

    if file is None:
        file = default_file

    with open(file,'rt') as f:
        config = s.load(f.read(), schema).data

    match which:
        case 'master':
            # extra validation
            if config['service_discovery_protocol']=='MDNS' and 'MDNS' not in config:
                raise ValueError("If the key 'service_discovery_protocol' is set to 'MDNS', the 'MDNS' key is required, but it was not found")
            elif config['service_discovery_protocol']=='SSDP' and 'SSDP' not in config:
                raise ValueError("If the key 'service_discovery_protocol' is set to 'SSDP', the 'SSDP' key is required, but it was not found")
            master = config
        case 'client':
            # extra validation
            if config['service_discovery_protocol']=='MDNS' and 'MDNS' not in config:
                raise ValueError("If the key 'service_discovery_protocol' is set to 'MDNS', the 'MDNS' key is required, but it was not found")
            elif config['service_discovery_protocol']=='SSDP' and 'SSDP' not in config:
                raise ValueError("If the key 'service_discovery_protocol' is set to 'SSDP', the 'SSDP' key is required, but it was not found")
            client = config
        case 'admin_server':
            admin_server = config