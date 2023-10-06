import pathlib

import labManager.common
import labManager.master

if __name__ == "__main__":
    path = pathlib.Path('.').resolve()
    if path.name=='example_scripts':
        path = path.parent

    labManager.common.config.load('master', path/'example_configs'/'master.yaml')
    labManager.master.run()
