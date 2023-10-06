import pathlib
import uvicorn

import labManager.utils
import labManager.utils.secrets
import labManager.admin_server

def create_app():
    path = pathlib.Path('.').resolve()
    if path.name=='example_scripts':
        path = path.parent

    labManager.utils.config.load('admin_server', path/'example_configs'/'admin_server.yaml')
    labManager.utils.secrets.load_secrets(path/'example_scripts'/'.env')   # see /example_configs/example.env for example file
    return labManager.admin_server.app

if __name__ == "__main__":
    uvicorn.run("admin_server:create_app", factory=True, reload=True)    # reload=True for development