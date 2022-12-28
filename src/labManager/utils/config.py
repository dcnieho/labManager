import strictyaml as s

master_config_file = 'master_config.yaml'
master_schema = s.Map({
    'network': s.Str(),
    'SMB': s.Map({
        'server': s.Str(),
        'domain': s.Str(),
        }),
    'toems': s.Map({
        'server': s.Str(),
        'port': s.Int(),
        }),
})
with open(master_config_file,'rt') as f:
    master = s.load(f.read(),master_schema).data


client_config_file = 'client_config.yaml'
client_schema = s.Map({
    'network': s.Str(),
})
with open(client_config_file,'rt') as f:
    client = s.load(f.read(),client_schema).data