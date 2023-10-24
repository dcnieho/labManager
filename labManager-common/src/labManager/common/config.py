import strictyaml as s
import pathlib

from . import task

_master_schema = s.Map({
    'network': s.Str(),
    'SSDP': s.Map({
        'device_type': s.Str(),
    }),
    s.Optional('projects'): s.Map({
        'name_table': s.MapPattern(s.Str(), s.Str()),
    }),
    'SMB': s.Map({
        'server': s.Str(),
        'domain': s.Str(),
        'projects': s.Map({
            'format': s.Str(),
            s.Optional('remove_trailing'): s.Str(),
        }),
    }),
    'admin': s.Map({
        'server': s.Str(),
        'port': s.Int(),
    }),
    'toems': s.Map({
        'server': s.Str(),
        'port': s.Int(),
        'images': s.Map({
            'format': s.Str(),
        }),
    }),
    s.Optional('login'): s.Map({
        'hint': s.Str(),
    }),
    s.Optional('clients'): s.Seq(
        s.Map({
            'name': s.Str(),
            'MAC' : s.Str(),
        })
    ),
    s.Optional('tasks'): s.Seq(
        s.Map({
            'name': s.Str(),
            'type': s.Enum(task.types),
            s.Optional('payload', default=''): s.Str(),
            s.Optional('payload_type', default='text'): s.Enum(['text','file']),
            s.Optional('cwd', default=''): s.Str(),
            s.Optional('env', default=''): s.Str(),
            s.Optional('interactive', default=False): s.Bool(),
        }),
    ),
})
_default_master_config_file = 'master.yaml'
master = None

_client_schema = s.Map({
    'network': s.Str(),
    'SSDP': s.Map({
        'device_type': s.Str(),
    }),
})
_default_client_config_file = 'client.yaml'
client = None

_admin_server_schema = s.Map({
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
            master = config
        case 'client':
            client = config
        case 'admin_server':
            admin_server = config