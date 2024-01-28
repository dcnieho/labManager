import pathlib
import uvicorn
import argparse
import ctypes

import labManager.common
import labManager.common.secrets
import labManager.admin_server

def create_app():
    parser = argparse.ArgumentParser(description="labManager admin-server")
    parser.add_argument('--hide', action='store_true', help="hide console window")
    args = parser.parse_args()

    if args.hide:
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)

    path = pathlib.Path('.').resolve()
    if path.name=='example-scripts':
        path = path.parent

    if (path / 'admin-server.yaml').is_file():
        config_file = path/'admin-server.yaml'
    else:
        config_file = path/'example-configs'/'admin-server.yaml'

    if (path / '.env').is_file():
        env_file = path/'.env'
    else:
        env_file = path/'example-scripts'/'.env'

    labManager.common.config.load('admin_server', config_file)
    labManager.common.secrets.load_secrets(env_file)   # see /example-configs/example.env for example file
    return labManager.admin_server.app

if __name__ == "__main__":
    uvicorn.run("admin_server:create_app", factory=True, host='0.0.0.0')