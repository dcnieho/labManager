import strictyaml as s
import os

_master_config_file = 'master_config.yaml'
_master_schema = s.Map({
    'network': s.Str(),
    'SSDP': s.Map({
        'device_type': s.Str(),
        }),
    'user_projects': s.Map({
        'format': s.Str(),
        s.Optional('remove_trailing'): s.Str(),
        }),
    'SMB': s.Map({
        'server': s.Str(),
        'domain': s.Str(),
        }),
    'toems': s.Map({
        'server': s.Str(),
        'port': s.Int(),
        }),
    s.Optional('clients'): s.Seq(
        s.Map({
            'name': s.Str(),
            'MAC' : s.Str(),
            })
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
    'server': s.Str(),
    'project_format': s.Str(),
})

if os.path.isfile(_server_config_file):
    with open(_server_config_file,'rt') as f:
        admin_server = s.load(f.read(),_server_schema).data
else:
    admin_server = None