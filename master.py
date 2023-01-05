import asyncio

from labManager import master
from labManager.utils import async_thread

if __name__ == "__main__":
    async_thread.setup()
    asyncio.run(master.run())
    async_thread.cleanup()
