![Downloads](https://static.pepy.tech/badge/labManager-master) ![PyPI Latest Release](https://img.shields.io/pypi/v/labManager-master.svg) ![Python version support](https://img.shields.io/pypi/pyversions/labManager-master.svg)

## labManager v1.0.5
System for managing multi-station multi-tenant lab setups - master

This package is part of the [labManager tools](https://github.com/dcnieho/labManager/tree/master), a collection of tools for managing behavioral research setups. Please see [here](https://github.com/dcnieho/labManager/tree/master) for more information.

### Installation
```bash
pip install labManager-master
# or
pip install labManager-master[GUI] # ①
```
1. use the `GUI` extra to install labManager-master’s GUI.

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
    'service': s.Str(),                                 # Service name to discover when using MDNS, e.g.,
}),                                                     # _master._labManager._tcp.local.

s.Optional('SSDP'): s.Map({
    'device_type': s.Str(),                             # Device type to announce and listen for when using SSDP, e.g.,
}),                                                     # urn:schemas-upnp-org:device:labManager

s.Optional('projects'): s.Map({                         # Table of alias names for projects (to enable showing more friendly
                                                        # names)
    'name_table': s.MapPattern(s.Str(), s.Str()),       # Example entry: `0000-01: Demo environment`
}),

s.Optional('base_image_name_table'):                    # Table of alias names for disk images (to enable showing more friendly
                                                        # names)
    s.MapPattern(s.Str(), s.Str()),                     # Example entry: `station_base: Windows station`
s.Optional('SMB'): s.Map({                              # If users have access to a central storage facility using an SMB,
                                                        # configuration about the server and how project shares are named on it
    'server': s.Str(),                                  # Server FQDN or IP address
    'domain': s.Str(),                                  # Domain in which users are found (may be overridden by LDAP reply)
    'projects': s.Map({                                 # Project-to-SMB share mapping config
        'format': s.Str(),                              # Regex to match shares that are for projects
        s.Optional('remove_trailing', default=''):      # Characters to remove from end of project share name to map the to
            s.Str(),                                    # project names
    }),
    'mount_share_on_client': s.Bool(),                  # Boolean indicating whether the project share should be mounted as a
                                                        # network drive on client machines once the client connects to this
                                                        # master.
    'mount_drive_letter': s.Str(),                      # Drive letter for mapping the network drive, if
                                                        # `mount_share_on_client` is enabled
    'mount_only_known_clients': s.Bool(),               # Issue command to mount the network share only for known clients (i.e.
}),                                                     # clients listed in the clients configuration section below), not for
                                                        # other machines that run a labManager client (to prevent snooping of
                                                        # the user's credentials)

s.Optional('admin'): s.Map({                            # Configuration about the labManager admin-server
    'server': s.Str(),                                  # Server FQDN or IP address
    'port': s.Int(),                                    # Server port
}),

s.Optional('toems'): s.Map({                            # Configuration about the Theopenem instance
    'server': s.Str(),                                  # Server FQDN or IP address
    'port': s.Int(),                                    # Server port
    'images': s.Map({                                   # Project-to-disk image mapping config
        'format': s.Str(),                              # Regex to match disk images that belong to a project
    }),
    s.Optional('pre_upload_script'): s.Str(),           # Script that will be configured to run in Theopenem's LIE imaging
                                                        # environment when uploading a disk image (at the BeforeImaging stage)
    s.Optional('image_info_script'): s.Str(),           # Script that will be configured to run in Theopenem's LIE imaging
                                                        # environment when deploying a disk image (at the AfterFileCopy stage)
    s.Optional('image_info_script_partition'):          # Partition on the disk image for which the `image_info_script` should
        s.Int(),                                        # run.
}),

s.Optional('login'): s.Map({
    'hint': s.Str(),                                    # login hint to show in the labManager master GUI
}),

s.Optional('clients'): s.Seq(                           # Configuration for known clients, e.g., fixed stations in a lab setup
    s.Map({
        'name': s.Str(),                                # Name by which station should be known. Example entry: STATION01
        'MAC' : s.CommaSeparated(s.Str()),              # One or multiple MAC addresses of the station. Example entry:
    })                                                  # 0C:9D:92:1F:E6:04, F4:E9:D4:73:6F:EC, F4:E9:D4:73:6F:ED
),

s.Optional('tasks'): s.Seq(                             # preconfigured tasks to be shown in the labManager master GUI
    s.Map({
        'name': s.Str(),                                # Name to show in task GUI, should be descriptive
        'type': s.Enum(task.types),                     # Task type, one of labManager.common.task.Type
        s.Optional('payload', default=''): s.Str(),     # Command or other payload to execute
        s.Optional('payload_type', default='text'):     # Payload type, either as text to directly execute or path to a file to
            s.Enum(['text','file']),                    # load the payload from
        s.Optional('cwd', default=''): s.Str(),         # CWD in which to execute the command
        s.Optional('env', default=''): s.Str(),         # environment variables to set when executing the command
        s.Optional('interactive', default=False):       # Boolean indicating whether this is an interactive task. If true,
            s.Bool(),                                   # users can send commands to the task as it is running (e.g., use cmd
                                                        # as a remote shell)
        s.Optional('python_unbuffered', default=False): # if true, appends the -u flag to commands running the python
            s.Bool(),                                   # executable to put it in unbuffered mode, so that any output is
    }),                                                 # directly written to stdout/stderr and can be remotely monitored. Does
),                                                      # nothing for task types other than task.Type.Python_module and
                                                        # task.Type.Python_script
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

### Acknowledgements

This project was made possible by funding from the [LMK foundation, Sweden](https://lmkstiftelsen.se/).
