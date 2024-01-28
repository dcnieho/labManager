import asyncio
import pathlib
import argparse
import ctypes

import labManager.client
import labManager.common


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="labManager client")
    parser.add_argument('--hide', action='store_true', help="hide console window")
    parser.add_argument('--log_to_console', action='store_true', help="log to console")
    args = parser.parse_args()

    if not args.log_to_console:
        # set up some simple logging to file
        import sys
        import tempfile
        temp_dir = pathlib.Path(tempfile.gettempdir())
        sys.stdout = open(temp_dir / "labManager_client_stdout.txt", "w")
        sys.stderr = open(temp_dir / "labManager_client_stderr.txt", "w")

    if args.hide:
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)

    path = pathlib.Path('.').resolve()
    if path.name=='example-scripts':
        path = path.parent

    if (path / 'client.yaml').is_file():
        config_file = path/'client.yaml'
    else:
        config_file = path/'example-configs'/'client.yaml'

    labManager.common.config.load('client', config_file)

    labManager.common.async_thread.setup()

    client = labManager.client.Client()
    try:
        asyncio.run(labManager.client.runner(client))
    except KeyboardInterrupt:
        pass

    labManager.common.async_thread.cleanup()
