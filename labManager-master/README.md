![PyPI Latest Release](https://img.shields.io/pypi/v/labManager-master.svg) ![Python version support](https://img.shields.io/pypi/pyversions/labManager-master.svg)

## labManager v1.0.0
System for managing multi-station multi-tenant lab setups - master

### Standalone deployment
One simple way to make a standalone install of the app is to download [WinPython](https://winpython.github.io/), e.g. the latest 3.10.x version.
I think this is recommended instead of using a system-wide or user installation of Python, so that users of the computer will not interfere with the Python distribution that runs the management tools.

Do as follows:
1. Download the dot version, not the full package, e.g. `Winpython64-3.10.11.1dot`.
2. Run the downloaded exe, which unzips the WinPython files.
3. Take the python folder from the unzipped files (e.g. `python-3.10.11.amd64`), you do not need the rest. This is your python distribution. Put it where you want on the disk.
4. Open a command prompt in the root of the Python installation. Install the wanted labManager packages into it using, e.g., `.\python.exe -m pip install labManager-client`.
5. Finally use the `python.exe` in the folder to execute your script, such as one of the [example scripts](https://github.com/dcnieho/labManager/tree/master/example_scripts).
