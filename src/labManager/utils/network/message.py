import struct
import json
import pathlib
from enum import Enum, auto
from typing import Dict

from .. import enum_helper, structs, task

SIZE_FMT    = '!I'
SIZE_BYTES  = struct.calcsize(SIZE_FMT)


    
    
@enum_helper.get('messages')
class Message(structs.AutoNameDash):
    QUIT        = auto()
    IDENTIFY    = auto()
    INFO        = auto()

    ## tasks
    # master -> client
    TASK_CREATE = auto()   # {ID, TYPE, PAYLOAD}
    # client -> master
    TASK_OUTPUT = auto()   # task (stdout or stderr) output: {ID, stream_type, output}
    TASK_UPDATE = auto()   # to send task status update (started running, errored, finished). Latter two include return code: {ID, status, Optional[return_code]}

@enum_helper.get('message types')
class Type(structs.AutoNameDash):
    SIMPLE      = auto()
    JSON        = auto()

type_map = {
    Message.QUIT        : Type.SIMPLE,
    Message.IDENTIFY    : Type.SIMPLE,
    Message.INFO        : Type.SIMPLE,
    Message.TASK_CREATE : Type.JSON,
    Message.TASK_OUTPUT : Type.JSON,
    Message.TASK_UPDATE : Type.JSON,
    }


# support for sending some custom types via json
class CustomTypeEncoder(json.JSONEncoder):
    def default(self, obj):
        if type(obj) in [task.Type, task.Status, task.StreamType]:
            return {"__enum__": f'task_{obj}'}
        elif isinstance(obj, pathlib.Path):
            return {"__pathlib.Path__": str(obj)}
        return json.JSONEncoder.default(self, obj)

def json_reconstitute(d):
    if "__enum__" in d:
        name, member = d["__enum__"].split(".")
        match name:
            case 'task_Type':
                return task.Type.get(member)
            case 'task_Status':
                return task.Status.get(member)
            case 'task_StreamType':
                return task.StreamType.get(member)
            case other:
                raise ValueError(f'unknown enum "{other}"')
    elif "__pathlib.Path__" in d:
        return pathlib.Path(d["__pathlib.Path__"])
    else:
        return d

def parse(type: Type, msg: str) -> str | Dict:
    if type_map[type]==Type.JSON:
        msg = json.loads(msg, object_hook=json_reconstitute)
    return msg

def prepare(type: Type, payload: str | Dict) -> str:
    if type_map[type]==Type.JSON:
        payload = json.dumps(payload, cls=CustomTypeEncoder)
    return payload