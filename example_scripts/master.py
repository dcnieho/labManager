import pathlib

import labManager.master
import labManager.common

if __name__ == "__main__":
    path = pathlib.Path('.').resolve()
    if path.name=='example_scripts':
        path = path.parent

    if (path / 'master.yaml').is_file():
        config_file = path/'master.yaml'
    else:
        config_file = path/'example_configs'/'master.yaml'

    labManager.common.config.load('master', config_file)
    labManager.master.run()
