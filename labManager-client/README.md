![Downloads](https://static.pepy.tech/badge/labManager-client) ![PyPI Latest Release](https://img.shields.io/pypi/v/labManager-client.svg) ![Python version support](https://img.shields.io/pypi/pyversions/labManager-client.svg)

## labManager v1.0.2
System for managing multi-station multi-tenant lab setups - client

This package is part of the [labManager tools](https://github.com/dcnieho/labManager/tree/master), a collection of tools for managing behavioral research setups. Please see [here](https://github.com/dcnieho/labManager/tree/master) for more information.

### Example script
An example script for running the labManager client script is [provided here](https://github.com/dcnieho/labManager/tree/master/example-scripts/client.py).

### Configuration
The below shows the schema used for parsing the configuration file for labManager client, using [StrictYAML’s notation](https://hitchdev.com/strictyaml/).
An [example configuration file](https://github.com/dcnieho/labManager/tree/master/example-configs/client.yaml) is also available.

```python
'network': s.Str(),                         # Network on which to discover clients, e.g. 10.0.1.0/24
'network_retry': s.Map({                    # Configuration for retrying to get network connection on startup.
                                            # Useful whenit may take some time for the network connection to
                                            # come up after the computer station starts
    'number_tries': s.Int(),                # Number of times to try
    'wait': s.Int(),                        # Wait duration (s) between tries
}),

'service_discovery_protocol':               # Protocol to use for client discovery, MDNS or SSDP
    s.Enum(['MDNS','SSDP']),

s.Optional('MDNS'): s.Map({
    'service': s.Str(),                     # Service name to discover when using MDNS, e.g.,
}),                                         # _master._labManager._tcp.local.

s.Optional('SSDP'): s.Map({
    'device_type': s.Str(),                 # Device type to announce and listen for when using SSDP, e.g.,
}),                                         # urn:schemas-upnp-org:device:labManager
```

### Standalone deployment
One simple way to make a standalone install of the app is to download [WinPython](https://winpython.github.io/), e.g. the latest 3.10.x version.
I think this is recommended instead of using a system-wide or user installation of Python, so that users of the computer are unlikely to accidentally interfere with the Python distribution that runs the management tools.

Do as follows:

1. Download the dot version, not the full package, e.g. `Winpython64-3.10.11.1dot`.
2. Run the downloaded exe, which unzips the WinPython files.
3. Take the python folder from the unzipped files (e.g. `python-3.10.11.amd64`), you do not need the rest. This is your python distribution. Put it where you want on the disk.
4. Open a command prompt in the root of the Python installation. Install the wanted labManager packages into it using, e.g., `.\python.exe -m pip install labManager-client`.
5. Finally use the `python.exe` in the folder to execute your script, such the [example script](https://github.com/dcnieho/labManager/tree/master/example-scripts/client.py) to launch this tool.

### Acknowledgements

This project was made possible by funding from the [LMK foundation, Sweden](https://lmkstiftelsen.se/).
