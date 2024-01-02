import asyncio
import pathlib
import argparse
import ctypes

import labManager.master
import labManager.common

async def run():
    # login and start server
    master = labManager.master.Master()
    want_login = input(f'Do you want to log in to a project? (y/n): ').casefold()=='y'
    if want_login:
        await labManager.master.cmd_login_flow(master)
    else:
        print('You didn\'t answer y, so not logging in')
    await master.start_server()

    # wait until a client connects (irrespective of how many are already connected, this waits for a new one)
    await asyncio.wait_for(master.add_waiter('client-connect-any'), timeout=None)
    # can also wait for a specific client by name
    # await asyncio.wait_for(master.add_waiter('client-connect-name', 'STATION01'), timeout=None)
    # can also wait for a specific number of clients to be connected (will not fire when there are more or less)
    # await asyncio.wait_for(master.add_waiter('client-connected-nr', 1), timeout=None)

    # print some info about connected clients
    client_id = None
    with master.clients_lock:
        for c in master.clients:
            if master.clients[c].online:    # is None if client is not online but in the list because it was configured as a known client
                if client_id is None:
                    client_id = c
                print(master.clients[c].online.host)
                if master.clients[c].online.eye_tracker and master.clients[c].online.eye_tracker.online:
                    print(f'  eye tracker: {master.clients[c].online.eye_tracker.model}')

    # start a task on all clients
    tg_id, _ = await master.run_task(labManager.common.task.Type.Shell_command, 'ping 8.8.8.8', '*')
    # wait until all tasks in this task group are done (i.e. all clients have executed this task)
    await asyncio.wait_for(master.add_waiter('task-group', tg_id), timeout=None)

    # start a task on the first client
    _, tsk_ids = await master.run_task(labManager.common.task.Type.Shell_command, 'echo "test"', master.clients[client_id].id)
    await asyncio.wait_for(master.add_waiter('task', tsk_ids[0]), timeout=None)
    # could instead wait for any task
    # await asyncio.wait_for(master.add_waiter('task-any'), timeout=None)

    # print output of first task as run on first client (task_refs are indexed by client id)
    task = master.task_groups[tg_id].tasks[master.clients[client_id].id]
    print(f'ran "{task.payload}" on {master.clients[task.client].name} ({master.clients[task.client].online.host}) which finished with exit code {task.return_code}, got:')
    print(task.output)

    # get some file listings on the client
    # make this waiter before the request to ensure no race condition
    fut1 = master.add_waiter('file-listing', 'root', client_id)
    fut2 = master.add_waiter('file-listing', 'C:\\', client_id)
    # fut3 = master.add_waiter('file-listing', '\\\\SERVER', client_id)
    await master.get_client_drives(master.clients[client_id])
    await master.get_client_file_listing(master.clients[client_id], 'C:\\')
    # can also requests shares on a SMB server, will be found under \\SERVER, for waiter and file_listings
    # await master.get_client_remote_shares(master.clients[client_id], 'SERVER')  # NB: supports SERVER, \\SERVER, \\SERVER\, //SERVER and //SERVER/
    await asyncio.wait_for(fut1, timeout=None)
    await asyncio.wait_for(fut2, timeout=None)
    # await asyncio.wait_for(fut3, timeout=None)
    print(master.clients[client_id].online.file_listings['root'])
    print(master.clients[client_id].online.file_listings['C:\\'])
    # print(master.clients[client_id].online.file_listings['\\\\SERVER'])

    # do some file actions on the client (NB: you should really be waiting for each before continuing, but since all these are immediate there is no problem)
    await master.make_client_folder(master.clients[client_id], 'C:\\test')
    await master.rename_client_file_folder(master.clients[client_id], 'C:\\test', 'C:\\test2')
    await master.copy_client_file_folder(master.clients[client_id], 'C:\\test2', 'C:\\test3')
    await master.move_client_file_folder(master.clients[client_id], 'C:\\test2', 'C:\\test4')
    await master.delete_client_file_folder(master.clients[client_id], 'C:\\tes:*?t2')
    action_id = await master.delete_client_file_folder(master.clients[client_id], 'C:\\test3')
    await asyncio.wait_for(master.add_waiter('file-action', action_id), timeout=None)
    await master.make_client_file(master.clients[client_id], r'C:\test4\test.txt')
    await master.rename_client_file_folder(master.clients[client_id], r'C:\test4\test.txt', r'C:\test4\test2.txt')
    await master.copy_client_file_folder(master.clients[client_id], r'C:\test4\test2.txt', r'C:\test4\test3.txt')
    await master.move_client_file_folder(master.clients[client_id], r'C:\test4\test2.txt', r'C:\test4\test4.txt')
    await master.delete_client_file_folder(master.clients[client_id], r'C:\test4\test2.txt')
    await master.delete_client_file_folder(master.clients[client_id], r'C:\test4\test3.txt')
    await master.delete_client_file_folder(master.clients[client_id], r'C:\test4\test4.txt')
    action_id = await master.delete_client_file_folder(master.clients[client_id], 'C:\\test4')
    # wait till last action is done
    await asyncio.wait_for(master.add_waiter('file-action', action_id), timeout=None)
    print(master.clients[client_id].online.file_actions[action_id])
    # waiting for an already finished action returns immediately
    await asyncio.wait_for(master.add_waiter('file-action', action_id), timeout=None)

    client_name = master.clients[client_id].name
    await master.broadcast(labManager.common.message.Message.QUIT)
    # wait until a client disconnects. Safest in this case is to do this by name as client may have disconnected due to the above call before we manage to register the waiter
    await asyncio.wait_for(master.add_waiter('client-disconnect-name', client_name), timeout=None)
    # can also wait for any client to disconnect
    # await asyncio.wait_for(master.add_waiter('client-disconnect-any'), timeout=None)
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
