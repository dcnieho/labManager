import pathlib

import labManager.utils
import labManager.master

if __name__ == "__main__":
    labManager.utils.config.load('master',pathlib.Path('.')/'example_configs'/'master.yaml')
    labManager.master.run()
