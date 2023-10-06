import asyncio
import pathlib

import labManager.client
import labManager.utils

if __name__ == "__main__":
    labManager.utils.config.load('client',pathlib.Path('.')/'example_configs'/'client.yaml')

    labManager.utils.async_thread.setup()
    asyncio.run(labManager.client.run())
    labManager.utils.async_thread.cleanup()
