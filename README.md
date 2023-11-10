[![PyPI Latest Release](https://img.shields.io/pypi/v/labManager-admin-server.svg?label=pypi%20labManager-admin-server)](https://pypi.org/project/labManager-admin-server/)
[![PyPI Latest Release](https://img.shields.io/pypi/v/labManager-client.svg?label=pypi%20labManager-client)](https://pypi.org/project/labManager-client/)
[![PyPI Latest Release](https://img.shields.io/pypi/v/labManager-common.svg?label=pypi%20labManager-common)](https://pypi.org/project/labManager-common/)
[![PyPI Latest Release](https://img.shields.io/pypi/v/labManager-master.svg?label=pypi%20labManager-master)](https://pypi.org/project/labManager-master/)

[![image](https://img.shields.io/pypi/pyversions/labManager-master.svg)](https://pypi.org/project/labManager-master/)

# labManager v0.6.1
Master/client software for managing multi-station multi-tenant lab setups.

## Standalone deployment
One simple way to make a standalone install of the app is to download [WinPython](https://winpython.github.io/), e.g. the latest 3.10.x version.
I think this is recommended instead of using a system-wide or or user installation of Python, so that user of the computer will not interfere with the distribution
running the management tools.

Do as follows:
1. Download the dot version, not the full package, e.g. `Winpython64-3.10.11.1dot`.
2. Run the downloaded exe, which unzips the WinPython files.
3. Take the python folder from the unzipped files (e.g. `python-3.10.11.amd64`), you do not need the rest. This is your python distribution. Put it where you want on the disk.
4. Install the wanted labManager packages into it using, e.g., `.\python.exe -m pip install labManager-client`.
5. Finally use the `python.exe` in the folder to execute your script, such as one of the [example scripts](example_scripts).