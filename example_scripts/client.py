import asyncio
import pathlib

import labManager.client
import labManager.common

if __name__ == "__main__":
    path = pathlib.Path('.').resolve()
    if path.name=='example_scripts':
        path = path.parent

    labManager.common.config.load('client', path/'example_configs'/'client.yaml')

    labManager.common.async_thread.setup()
    asyncio.run(labManager.client.run())
    labManager.common.async_thread.cleanup()
