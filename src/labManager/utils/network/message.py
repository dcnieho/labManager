import struct
import json
import pathlib
import sys
import importlib
from enum import auto, Enum
from typing import Dict

from .. import enum_helper, structs

SIZE_FMT    = '!I'
SIZE_BYTES  = struct.calcsize(SIZE_FMT)


    
    
@enum_helper.get('messages')
class Message(structs.AutoNameDash):
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
class Type(structs.AutoNameDash):
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


# support for sending some custom types via json
class CustomTypeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj,Enum):
            mname = obj.__class__.__module__
            ename = obj.__class__.__qualname__
            member= obj.name
            name  = f'{mname}.{ename}.{member}'
            return {"__enum__": name}
        elif isinstance(obj, pathlib.Path):
            return {"__pathlib.Path__": str(obj)}
        return json.JSONEncoder.default(self, obj)

def json_reconstitute(d):
    if "__enum__" in d:
        mname, ename, member = d["__enum__"].rsplit(".", 2)
        if not (module := sys.modules.get(mname)):
            module = importlib.import_module(mname)
        return getattr(getattr(module,ename), member)
    elif "__pathlib.Path__" in d:
        return pathlib.Path(d["__pathlib.Path__"])
    else:
        return d

def parse(type: Type, msg: str) -> str | Dict:
    # load from JSON if needed
    if type_map[type]==Type.JSON:
        msg = json.loads(msg, object_hook=json_reconstitute)
    return msg

def prepare(type: Type, payload: str | Dict) -> str:
    # dump to JSON if needed
    if type_map[type]==Type.JSON:
        payload = json.dumps(payload, cls=CustomTypeEncoder)
    return payload