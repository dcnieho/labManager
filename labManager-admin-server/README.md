![Downloads](https://static.pepy.tech/badge/labManager-admin-server) ![PyPI Latest Release](https://img.shields.io/pypi/v/labManager-admin-server.svg) ![Python version support](https://img.shields.io/pypi/pyversions/labManager-admin-server.svg)

## labManager v1.0.5
System for managing multi-station multi-tenant lab setups - admin-server

This package is part of the [labManager tools](https://github.com/dcnieho/labManager/tree/master), a collection of tools for managing behavioral research setups. Please see [here](https://github.com/dcnieho/labManager/tree/master) for more information.

### Installation
```bash
pip install labManager-admin-server
```

### Example script
An example script for running the labManager admin-server script is [provided here](https://github.com/dcnieho/labManager/tree/master/example-scripts/admin-server.py).

### Configuration
The below shows the schema used for parsing the configuration file for labManager admin-server, using [StrictYAML’s notation](https://hitchdev.com/strictyaml/).
An [example configuration file](https://github.com/dcnieho/labManager/tree/master/example-configs/admin-server.yaml) is also available.

```python
'LDAP': s.Map({                             # Configuration about the LDAP server and querying it
    'server': s.Str(),                      # Server FQDN or IP address
    'projects': s.Map({
        'format': s.Str(),                  # Regex to match projects in memberOf field of found user
    }),
}),
'toems': s.Map({                            # Configuration about the Theopenem instance
    'server': s.Str(),                      # Server FQDN or IP address
    'port': s.Int(),                        # Server port
    'images': s.Map({
        'format': s.Str(),                  # Regex to match disk images that belong to a project
        'file_copy_actions': s.Seq(         # List of file copy actions to activate when a new disk
            s.Map({                         # image is created
                'name': s.Str(),            # Name in Theopenem of a file_copy action
                'partition_id': s.Int(),    # Partition on the disk image for which the action should
            }),                             # be run
        ),
        'standard': s.Seq(                  # list of "standard" disk images that should be visible
            s.Str(),                        # (read only) to all projects. Use to, for instance,
        ),                                  # provide a base image that new projects can start from.
    }),
}),
```

#### Secrets file
Besides the configuration yaml file, the admin-server tool also needs a secrets file containing credentials with admin privileges for the LDAP and Theopenem environments.
It should be formatted as follows:

```dosini
LDAP_ACCOUNT    = username
LDAP_PASSWORD   = password
LDAP_SEARCH_BASE= OU=something,DC=something

TOEMS_ACCOUNT   = username
TOEMS_PASSWORD  = password
```

### Standalone deployment
One simple way to make a standalone install of the app is to download [WinPython](https://winpython.github.io/), e.g. the latest 3.10.x version.
I think this is recommended instead of using a system-wide or user installation of Python, so that users of the computer are unlikely to accidentally interfere with the Python distribution that runs the management tools.

Do as follows:

1. Download the dot version, not the full package, e.g. `Winpython64-3.10.11.1dot`.
2. Run the downloaded exe, which unzips the WinPython files.
3. Take the python folder from the unzipped files (e.g. `python-3.10.11.amd64`), you do not need the rest. This is your python distribution. Put it where you want on the disk.
4. Open a command prompt in the root of the Python installation. Install the wanted labManager packages into it using, e.g., `.\python.exe -m pip install labManager-admin-server`.
5. Finally use the `python.exe` in the folder to execute your script, such the [example script](https://github.com/dcnieho/labManager/tree/master/example-scripts/admin-server.py) to launch this tool.

### Acknowledgements

This project was made possible by funding from the [LMK foundation, Sweden](https://lmkstiftelsen.se/).
