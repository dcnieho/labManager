import pathlib
import uvicorn

import labManager.common
import labManager.common.secrets
import labManager.admin_server

def create_app():
    path = pathlib.Path('.').resolve()
    if path.name=='example_scripts':
        path = path.parent

    if (path / 'admin_server.yaml').is_file():
        config_file = path/'admin_server.yaml'
    else:
        config_file = path/'example_configs'/'admin_server.yaml'

    if (path / '.env').is_file():
        env_file = path/'.env'
    else:
        env_file = path/'example_scripts'/'.env'

    labManager.common.config.load('admin_server', config_file)
    labManager.common.secrets.load_secrets(env_file)   # see /example_configs/example.env for example file
    return labManager.admin_server.app

if __name__ == "__main__":
    uvicorn.run("admin_server:create_app", factory=True, host='0.0.0.0')