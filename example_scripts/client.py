import asyncio
import pathlib

import labManager.client
import labManager.utils

if __name__ == "__main__":
    path = pathlib.Path('.').resolve()
    if path.name=='example_scripts':
        path = path.parent

    labManager.utils.config.load('client', path/'example_configs'/'client.yaml')

    labManager.utils.async_thread.setup()
    asyncio.run(labManager.client.run())
    labManager.utils.async_thread.cleanup()
