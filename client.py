import asyncio

from labManager import client
from labManager.utils import async_thread

if __name__ == "__main__":
    async_thread.setup()
    asyncio.run(client.run())
    async_thread.cleanup()
