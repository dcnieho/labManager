import asyncio
import pathlib
import argparse
import ctypes

import labManager.master
import labManager.common

async def run():
    # login and start server
    master = labManager.master.Master()
    await labManager.master.cmd_login_flow(master)
    await master.start_server()

    # wait until a client connects
    await asyncio.wait_for(master.add_waiter('client-connect', None), timeout=None)

    # print some info about each client
    with master.clients_lock:
        for c in master.clients:
            print(master.clients[c].online.host)
            if master.clients[c].online.eye_tracker and master.clients[c].online.eye_tracker.online:
                print(f'  eye tracker: {master.clients[c].online.eye_tracker.model}')

    # start a task on all clients
    tg_id, _ = await master.run_task(labManager.common.task.Type.Shell_command, 'ping 8.8.8.8', '*')
    tasks = [master.task_groups[tg_id].task_refs[c] for c in master.task_groups[tg_id].task_refs]

    # wait until tasks are done
    while any([not t.done() for t in tasks]):
        await asyncio.sleep(.5)

    # print output of first task
    print(f'ran "{tasks[0].payload}" on {master.clients[c].online.host} which finished with exit code {tasks[0].return_code}, got:')
    print(tasks[0].output)

    # clean up
    await master.stop_server()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="labManager client")
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

    # run in separate thread (part of labManager.master's actions
    # anyway run in the separate thread's loop provided by async_thread
    # so to keep it simple in this example we just run everything from there)
    labManager.common.async_thread.setup()
    labManager.common.async_thread.wait(run())
    labManager.common.async_thread.cleanup()
