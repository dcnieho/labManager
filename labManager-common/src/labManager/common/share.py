import asyncio
import subprocess
from . import async_thread

def mount_share(drive, share_path, user, password):
    # net use drive: share_path password /user:user /p:no
    call = f'net use {drive}: {share_path} {password} /user:{user} /p:no'
    if not async_thread.loop or not async_thread.loop.is_running:
        subprocess.Popen(call, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=True)
    else:
        async_thread.run(asyncio.create_subprocess_shell(call, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))

def unmount_share(drive):
    # net use drive: /delete
    call = f'net use {drive}: /delete'
    if not async_thread.loop or not async_thread.loop.is_running:
        subprocess.Popen(call, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=True)
    else:
        async_thread.run(asyncio.create_subprocess_shell(call, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))