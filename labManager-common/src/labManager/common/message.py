import struct
import jsonpickle
from enum import auto

from . import enum_helper

SIZE_FMT    = '!I'
SIZE_BYTES  = struct.calcsize(SIZE_FMT)



@enum_helper.get
class Message(enum_helper.AutoNameDash):
    QUIT                = auto()    # tell client to kill its handler for this connection

    ## queries
    IDENTIFY            = auto()

    ## eye tracker
    ET_STATUS_REQUEST   = auto()
    ET_STATUS_INFORM    = auto()
    ET_ATTR_REQUEST     = auto()    # request eye tracker attribute(s) (including if there is one connected)
    ET_ATTR_UPDATE      = auto()    # inform about change in eye tracker attribute(s)
    ET_EVENT            = auto()

    ## share mounting
    SHARE_MOUNT         = auto()    # request client to mount specified share with specified credentials
    SHARE_UNMOUNT       = auto()    # request client to unmount specified share

    ## tasks
    # master -> client
    TASK_CREATE         = auto()    # {task_id, type, payload, cwd, env} # payload is the executable and args of subprocess.Popen, cwd and env (optional) its cwd and env arguments
    TASK_INPUT          = auto()    # if you have an interactive task (e.g. shell, or some other process listening to stdin), you can send commands to it using this message type
    TASK_CANCEL         = auto()    # cancel running or pending task
    # client -> master
    TASK_OUTPUT         = auto()    # {task_id, stream_type, output}, task (stdout or stderr) output
    TASK_UPDATE         = auto()    # {task_id, status, Optional[return_code]}, task status update (started running, errored, finished). Latter two include return code

    ## file browsing
    # master -> client
    FILE_GET_DRIVES     = auto()    # request information about known local harddrives and network names
    FILE_GET_SHARES     = auto()    # {net_name, user, password, domain, access_level} request accessible shares at a network name (user Guest without password is used if not provided)
    FILE_GET_LISTING    = auto()    # {path} request contents of a local path
    # client -> master
    FILE_LISTING        = auto()    # {path, dir_list}: listing of directories and files at path. path may be 'root' when listing accessible drives and net_names, or a \\net_name when listing shares for a network computer

    ## file actions (NB: local paths below includes network shares accessible by the client)
    # master -> client
    FILE_MAKE           = auto()    # {path, is_dir, action_id} request creation of a local path (empty file or directory)
    FILE_RENAME         = auto()    # {old_path, new_path, action_id} request renaming of a local path
    FILE_COPY_MOVE      = auto()    # {source_path, dest_path, is_move, action_id} request a copy or move between two local paths
    FILE_DELETE         = auto()    # {path, action_id} request deleting a path
    # client -> master
    FILE_ACTION_STATUS  = auto()    # {path, action_id, action, status...} status update for file actions


@enum_helper.get
class Type(enum_helper.AutoNameDash):
    SIMPLE      = auto()
    JSON        = auto()
    BINARY      = auto()    # binary message, don't encode() or decode()

type_map = {
    Message.QUIT                : Type.SIMPLE,
    Message.IDENTIFY            : Type.JSON,

    Message.ET_STATUS_REQUEST   : Type.SIMPLE,
    Message.ET_STATUS_INFORM    : Type.JSON,
    Message.ET_ATTR_REQUEST     : Type.JSON,
    Message.ET_ATTR_UPDATE      : Type.JSON,
    Message.ET_EVENT            : Type.JSON,

    Message.SHARE_MOUNT         : Type.JSON,
    Message.SHARE_UNMOUNT       : Type.JSON,

    Message.TASK_CREATE         : Type.JSON,
    Message.TASK_INPUT          : Type.JSON,
    Message.TASK_CANCEL         : Type.JSON,
    Message.TASK_OUTPUT         : Type.JSON,
    Message.TASK_UPDATE         : Type.JSON,

    Message.FILE_GET_DRIVES     : Type.JSON,
    Message.FILE_GET_SHARES     : Type.JSON,
    Message.FILE_GET_LISTING    : Type.JSON,
    Message.FILE_LISTING        : Type.JSON,

    Message.FILE_MAKE           : Type.JSON,
    Message.FILE_RENAME         : Type.JSON,
    Message.FILE_COPY_MOVE      : Type.JSON,
    Message.FILE_DELETE         : Type.JSON,
    Message.FILE_ACTION_STATUS  : Type.JSON,
    }


def parse(type: Type, msg: str) -> str | dict:
    # load from JSON if needed
    if type_map[type]==Type.JSON:
        msg = jsonpickle.decode(msg, keys=True)
    return msg

def prepare(type: Type, payload: str | bytes | dict) -> str:
    # dump to JSON if needed
    if type_map[type]==Type.JSON:
        payload = jsonpickle.encode(payload, keys=True)
    return payload