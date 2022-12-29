import strictyaml as s
import os

master_config_file = 'master_config.yaml'
master_schema = s.Map({
    'network': s.Str(),
    'SSDP': s.Map({
        'device_type': s.Str(),
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

if os.path.isfile(master_config_file):
    with open(master_config_file,'rt') as f:
        master = s.load(f.read(),master_schema).data
else:
    master = None


client_config_file = 'client_config.yaml'
client_schema = s.Map({
    'network': s.Str(),
    'SSDP': s.Map({
        'device_type': s.Str(),
        }),
})

if os.path.isfile(client_config_file):
    with open(client_config_file,'rt') as f:
        client = s.load(f.read(),client_schema).data
else:
    client = None