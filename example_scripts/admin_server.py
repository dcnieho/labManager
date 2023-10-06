import pathlib
import uvicorn

import labManager.utils

if __name__ == "__main__":
    labManager.utils.config.load('admin_server',pathlib.Path('.')/'example_configs'/'admin_server.yaml')
    uvicorn.run("labManager.admin_server:app", reload=True)    # reload=True for development