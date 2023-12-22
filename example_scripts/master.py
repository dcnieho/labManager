import pathlib
import argparse
import ctypes

import labManager.master
import labManager.common

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="labManager master")
    parser.add_argument('--hide', action='store_true', help="hide console window")
    args = parser.parse_args()

    if args.hide:
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)

    path = pathlib.Path('.').resolve()
    if path.name=='example_scripts':
        path = path.parent

    if (path / 'master.yaml').is_file():
        config_file = path/'master.yaml'
    else:
        config_file = path/'example_configs'/'master.yaml'

    labManager.common.config.load('master', config_file)

    labManager.master.set_up()
    labManager.master.run_GUI()
    labManager.master.clean_up()
