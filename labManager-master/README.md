![PyPI Latest Release](https://img.shields.io/pypi/v/labManager-master.svg) ![Python version support](https://img.shields.io/pypi/pyversions/labManager-master.svg)

## labManager v1.0.0
System for managing multi-station multi-tenant lab setups - master

### Example script
An example script for running the labManager master script is [provided here](https://github.com/dcnieho/labManager/tree/master/example-scripts/master.py).

### Configuration
The below shows the schema used for parsing the configuration file for labManager master, using [StrictYAML’s notation](https://hitchdev.com/strictyaml/).
An [example configuration file](https://github.com/dcnieho/labManager/tree/master/example-configs/master.yaml) is also available.

```python
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
```

### Standalone deployment
One simple way to make a standalone install of the app is to download [WinPython](https://winpython.github.io/), e.g. the latest 3.10.x version.
I think this is recommended instead of using a system-wide or user installation of Python, so that users of the computer are unlikely to accidentally interfere with the Python distribution that runs the management tools.

Do as follows:

1. Download the dot version, not the full package, e.g. `Winpython64-3.10.11.1dot`.
2. Run the downloaded exe, which unzips the WinPython files.
3. Take the python folder from the unzipped files (e.g. `python-3.10.11.amd64`), you do not need the rest. This is your python distribution. Put it where you want on the disk.
4. Open a command prompt in the root of the Python installation. Install the wanted labManager packages into it using, e.g., `.\python.exe -m pip install labManager-master`.
5. Finally use the `python.exe` in the folder to execute your script, such the [example script](https://github.com/dcnieho/labManager/tree/master/example-scripts/master.py) to launch this tool.
