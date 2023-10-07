import struct
import jsonpickle
from enum import auto
from typing import Dict

from . import enum_helper

SIZE_FMT    = '!I'
SIZE_BYTES  = struct.calcsize(SIZE_FMT)



@enum_helper.get('messages')
class Message(enum_helper.AutoNameDash):
    QUIT                = auto()    # tell client to kill its handler for this connection
    INFO                = auto()

    ## queries
    IDENTIFY            = auto()

    ## eye tracker
    ET_STATUS_REQUEST   = auto()
    ET_STATUS_INFORM    = auto()
    ET_ATTR_REQUEST     = auto()    # request eye tracker attribute(s) (including if there is one connected)
    ET_ATTR_UPDATE      = auto()    # inform about change in eye tracker attribute(s)
    ET_EVENT            = auto()

    ## tasks
    # master -> client
    TASK_CREATE         = auto()    # {task_id, type, payload, cwd, env} # payload is the executable and args of subprocess.Popen, cwd and env (optional) its cwd and env arguments
    TASK_INPUT          = auto()    # if you have an interactive task (e.g. shell, or some other process listening to stdin), you can send commands to it using this message type
    TASK_CANCEL         = auto()    # cancel running or pending task
    # client -> master
    TASK_OUTPUT         = auto()    # {task_id, stream_type, output}, task (stdout or stderr) output
    TASK_UPDATE         = auto()    # {task_id, status, Optional[return_code]}, task status update (started running, errored, finished). Latter two include return code

@enum_helper.get('message types')
class Type(enum_helper.AutoNameDash):
    SIMPLE      = auto()
    JSON        = auto()

type_map = {
    Message.QUIT                : Type.SIMPLE,
    Message.INFO                : Type.SIMPLE,
    Message.IDENTIFY            : Type.JSON,

    Message.ET_STATUS_REQUEST   : Type.SIMPLE,
    Message.ET_STATUS_INFORM    : Type.JSON,
    Message.ET_ATTR_REQUEST     : Type.JSON,
    Message.ET_ATTR_UPDATE      : Type.JSON,
    Message.ET_EVENT            : Type.JSON,

    Message.TASK_CREATE         : Type.JSON,
    Message.TASK_INPUT          : Type.JSON,
    Message.TASK_CANCEL         : Type.JSON,
    Message.TASK_OUTPUT         : Type.JSON,
    Message.TASK_UPDATE         : Type.JSON,
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