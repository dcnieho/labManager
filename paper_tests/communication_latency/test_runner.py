import asyncio
import pathlib
import numpy as np
from dataclasses import dataclass

import labManager.master
import labManager.common
import labManager.common.task as task

BASE_PORT = 23234
BASE_IP   = '10.0.1.'
CWD       = 'C:\\utils\\time'

@dataclass
class TestTask:
    frequency: float
    duration: int
    num_char: int

task_list: list[TestTask] = [
    TestTask(1200., 60, 16),
    TestTask(1200., 60, 64),
    TestTask(1200., 60, 256),
    TestTask(1200., 60, 1024),
    TestTask(1200., 60, 5120),
    TestTask(4800., 60, 1024),
    TestTask(4800., 60, 5120),
]

gui_container = labManager.master.GUIContainer()

async def runner(master: labManager.master.Master):
    print('starting server for communicating with clients... ', end='')
    await master.start_server()
    print('done')

    # wait until all the configured clients have connected
    n_clients = len(master.clients)
    print('waiting for clients... ', end='')
    await asyncio.wait_for(master.add_waiter('client-connected-nr', n_clients), timeout=None)
    print('all clients have connected')

    # stop time service on all machines as we don't want it adjusting the clocks during our measurement
    tg_id, _ = await master.run_task('Shell command', 'net stop w32time', '*')
    await asyncio.wait_for(master.add_waiter('task-group', tg_id), timeout=None)

    # get what clients we have
    ports = [BASE_PORT+i for i in range(n_clients-1)]
    with master.clients_lock:
        client_ids = [master.clients[c].id for c in master.clients]
        client_ips = [master.clients[c].online.host.removeprefix(BASE_IP) for c in master.clients]

    # figure out connections between clients
    source_to_sink = np.array([np.mod(c+np.arange(1,n_clients),n_clients) for c in range(n_clients)])
    sink_to_source = np.array([np.mod(c-np.arange(1,n_clients),n_clients) for c in range(n_clients)])

    frequencies = ' '.join([str(t.frequency) for t in task_list])
    durations   = ' '.join([str(t.duration)  for t in task_list])
    n_chars     = ' '.join([str(t.num_char)  for t in task_list])

    # launch all the tasks
    coros = []
    for i in range(source_to_sink.shape[0]):
        for j in range(source_to_sink.shape[1]):
            sink   = client_ips[source_to_sink[i,j]]
            cmd    = f'{CWD}\\python -u .\\tcp_source.py {sink} --base_ip {BASE_IP} -p {ports[j]} -d {durations} -f {frequencies} -n {n_chars}'
            coros.append(master.run_task('Process exec', cmd, client_ids[i], cwd=CWD))
    for i in range(source_to_sink.shape[0]):
        for j in range(source_to_sink.shape[1]):
            source = client_ips[sink_to_source[i,j]]
            cmd    = f'{CWD}\\python -u .\\tcp_sink.py {source} --base_ip {BASE_IP} -p {ports[j]}'
            coros.append(master.run_task('Process exec', cmd, client_ids[i], cwd=CWD))

    # schedule tasks and get task ids
    print('scheduling tasks... ', end='')
    res = await asyncio.gather(*coros)
    task_ids = [tid for tids in res for tid in tids[1]]
    print('done')

    # wait till all are done
    print('running tasks... ', end='')
    waiters = [master.add_waiter('task', tid) for tid in task_ids]
    await asyncio.gather(*waiters)
    print('done')

    # collect data
    print('copying data to server... ', end='')
    coros = [master.copy_client_file_folder(master.clients[cid], f"{CWD}\\data", "\\\\SERVER\\scratch\\Dee\\time_data", dirs_exist_ok=True) for cid in client_ids]
    action_ids = await asyncio.gather(*coros)
    # NB: no need to wait, user can just see in GUI whether tasks are done, and close GUI to end this program once done. But lets do it anyway
    waiters = [master.add_waiter('file-action', aid) for aid in action_ids]
    await asyncio.gather(*waiters)
    print('done')


if __name__ == "__main__":
    config_file = pathlib.Path('runner.yaml').resolve()
    labManager.common.config.load('master', config_file)

    # start master
    labManager.common.async_thread.setup()
    master = labManager.master.Master()
    master.load_known_clients()
    fut = labManager.common.async_thread.run(runner(master))
    # attach a GUI
    labManager.master.run_GUI(master, gui_container)
    # in case user closes GUI before runner is done, wait for runner to finish
    if not fut.done():
        fut.result()
    # stop server
    print('stopping server... ', end='')
    labManager.common.async_thread.wait(master.stop_server())
    print('done')
    labManager.common.async_thread.cleanup()
