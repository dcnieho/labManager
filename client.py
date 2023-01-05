import asyncio

from labManager.utils import config, network


async def main():
    client = network.client.Client(config.client['network'])
    await client.start(keep_ssdp_running=True)

    # run until client finished
    await asyncio.sleep(3600)

    # this should be a no-op, but to be sure:
    # shut down client, wait for it to quit
    await client.stop()

if __name__ == "__main__":
    asyncio.run(main())
