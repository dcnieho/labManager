import strictyaml as s
import os

from . import task

_master_config_file = 'master_config.yaml'
_master_schema = s.Map({
    'network': s.Str(),
    'SSDP': s.Map({
        'device_type': s.Str(),
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
            'payload': s.Str(),
            s.Optional('cwd'): s.Str(),
            s.Optional('env'): s.Str(),
            s.Optional('interactive', default=False): s.Bool(),
        }),
    ),
})

if os.path.isfile(_master_config_file):
    with open(_master_config_file,'rt') as f:
        master = s.load(f.read(),_master_schema).data
else:
    master = None


_client_config_file = 'client_config.yaml'
_client_schema = s.Map({
    'network': s.Str(),
    'SSDP': s.Map({
        'device_type': s.Str(),
    }),
})

if os.path.isfile(_client_config_file):
    with open(_client_config_file,'rt') as f:
        client = s.load(f.read(),_client_schema).data
else:
    client = None


_server_config_file = 'admin_server_config.yaml'
_server_schema = s.Map({
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

if os.path.isfile(_server_config_file):
    with open(_server_config_file,'rt') as f:
        admin_server = s.load(f.read(),_server_schema).data
else:
    admin_server = None