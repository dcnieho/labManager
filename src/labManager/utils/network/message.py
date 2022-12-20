import struct
import json
import pathlib
from enum import Enum, auto
from typing import Dict

from .. import structs

SIZE_FMT    = '!I'
SIZE_BYTES  = struct.calcsize(SIZE_FMT)

class AutoMessageNaming(Enum):
    def _generate_next_value_(name, start, count, last_values):
        return name.lower().strip("_").replace("_", "-")

    
def enum_get(name: str):
    def decorator_get(cls):
        def get(value: str):
            if isinstance(value, cls):
                return value

            if isinstance(value, str) and value in [e.value for e in cls]:
                return cls(value)
            else:
                raise ValueError(f"The provided input should be a string identifying one of the known {name}.\nUnderstood values: {[e.value for e in cls]}.\nGot: {value}")

        setattr(cls, 'get', get)
        return cls
    return decorator_get
    
@enum_get('messages')
class Message(AutoMessageNaming):
    QUIT        = auto()
    IDENTIFY    = auto()
    INFO        = auto()

    ## tasks
    # master -> client
    TASK_CREATE = auto()   # {ID, TYPE, PAYLOAD}
    # client -> master
    TASK_OUTPUT = auto()   # task (stdout or stderr) output: {ID, output}
    TASK_UPDATE = auto()   # to send task status update (started running, errored, finished). Latter two include return code: {ID, status}

@enum_get('message types')
class Type(AutoMessageNaming):
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
        if type(obj) in [structs.TaskType, structs.Status]:
            return {"__enum__": f'structs_{obj}'}
        elif isinstance(obj, pathlib.Path):
            return {"__pathlib.Path__": str(obj)}
        return json.JSONEncoder.default(self, obj)

def json_reconstitute(d):
    if "__enum__" in d:
        name, member = d["__enum__"].split(".")
        match name:
            case 'structs_TaskType':
                return getattr(structs.TaskType, member)
            case 'structs_Status':
                return getattr(structs.Status, member)
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