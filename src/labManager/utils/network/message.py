import struct
import jsonpickle
from enum import auto
from typing import Dict

from .. import enum_helper

SIZE_FMT    = '!I'
SIZE_BYTES  = struct.calcsize(SIZE_FMT)


    
    
@enum_helper.get('messages')
class Message(enum_helper.AutoNameDash):
    QUIT        = auto()    # tell client to kill its handler for this connection
    IDENTIFY    = auto()
    INFO        = auto()

    ## tasks
    # master -> client
    TASK_CREATE = auto()    # {task_id, type, payload, cwd, env} # payload is the executable and args of subprocess.Popen, cwd and env (optional) its cwd and env arguments
    # client -> master
    TASK_OUTPUT = auto()    # {task_id, stream_type, output}, task (stdout or stderr) output
    TASK_UPDATE = auto()    # {task_id, status, Optional[return_code]}, task status update (started running, errored, finished). Latter two include return code

@enum_helper.get('message types')
class Type(enum_helper.AutoNameDash):
    SIMPLE      = auto()
    JSON        = auto()

type_map = {
    Message.QUIT        : Type.SIMPLE,
    Message.IDENTIFY    : Type.JSON,
    Message.INFO        : Type.SIMPLE,
    Message.TASK_CREATE : Type.JSON,
    Message.TASK_OUTPUT : Type.JSON,
    Message.TASK_UPDATE : Type.JSON,
    }


def parse(type: Type, msg: str) -> str | Dict:
    # load from JSON if needed
    if type_map[type]==Type.JSON:
        msg = jsonpickle.decode(msg, keys=True)
    return msg

def prepare(type: Type, payload: str | Dict) -> str:
    # dump to JSON if needed
    if type_map[type]==Type.JSON:
        payload = jsonpickle.encode(payload, keys=True)
    return payload