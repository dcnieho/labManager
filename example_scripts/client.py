import asyncio
import pathlib
import ctypes

import labManager.client
import labManager.common

if __name__ == "__main__":
    ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)

    path = pathlib.Path('.').resolve()
    if path.name=='example_scripts':
        path = path.parent

    if (path / 'client.yaml').is_file():
        config_file = path/'client.yaml'
    else:
        config_file = path/'example_configs'/'client.yaml'

    labManager.common.config.load('client', config_file)

    labManager.common.async_thread.setup()
    try:
        asyncio.run(labManager.client.run())
    except KeyboardInterrupt:
        pass
    labManager.common.async_thread.cleanup()
