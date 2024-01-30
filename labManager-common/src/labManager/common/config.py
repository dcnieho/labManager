import strictyaml as s
import pathlib

from . import task

_master_schema = s.Map({
# tag::master_schema[]
    'network': s.Str(),                                     # Network on which to discover clients, e.g. 10.0.1.0/24

    'service_discovery_protocol':                           # Protocol to use for client discovery, MDNS or SSDP
        s.Enum(['MDNS','SSDP']),

    s.Optional('MDNS'): s.Map({
        'service': s.Str(),                                 # Service name to discover when using MDNS, e.g.,
    }),                                                     # _master._labManager._tcp.local.

    s.Optional('SSDP'): s.Map({
        'device_type': s.Str(),                             # Device type to announce and listen for when using SSDP, e.g.,
    }),                                                     # urn:schemas-upnp-org:device:labManager

    s.Optional('projects'): s.Map({                         # Table of alias names for projects (to enable showing more friendly
                                                            # names)
        'name_table': s.MapPattern(s.Str(), s.Str()),       # Example entry: `0000-01: Demo environment`
    }),

    s.Optional('base_image_name_table'):                    # Table of alias names for disk images (to enable showing more friendly
                                                            # names)
        s.MapPattern(s.Str(), s.Str()),                     # Example entry: `station_base: Windows station`
    s.Optional('SMB'): s.Map({                              # If users have access to a central storage facility using an SMB,
                                                            # configuration about the server and how project shares are named on it
        'server': s.Str(),                                  # Server FQDN or IP address
        'domain': s.Str(),                                  # Domain in which users are found (may be overridden by LDAP reply)
        'projects': s.Map({                                 # Project-to-SMB share mapping config
            'format': s.Str(),                              # Regex to match shares that are for projects
            s.Optional('remove_trailing', default=''):      # Characters to remove from end of project share name to map the to
                s.Str(),                                    # project names
        }),
        'mount_share_on_client': s.Bool(),                  # Boolean indicating whether the project share should be mounted as a
                                                            # network drive on client machines once the client connects to this
                                                            # master.
        'mount_drive_letter': s.Str(),                      # Drive letter for mapping the network drive, if
                                                            # `mount_share_on_client` is enabled
        'mount_only_known_clients': s.Bool(),               # Issue command to mount the network share only for known clients (i.e.
    }),                                                     # clients listed in the clients configuration section below), not for
                                                            # other machines that run a labManager client (to prevent snooping of
                                                            # the user's credentials)

    s.Optional('admin'): s.Map({                            # Configuration about the labManager admin-server
        'server': s.Str(),                                  # Server FQDN or IP address
        'port': s.Int(),                                    # Server port
    }),

    s.Optional('toems'): s.Map({                            # Configuration about the Theopenem instance
        'server': s.Str(),                                  # Server FQDN or IP address
        'port': s.Int(),                                    # Server port
        'images': s.Map({                                   # Project-to-disk image mapping config
            'format': s.Str(),                              # Regex to match disk images that belong to a project
        }),
        s.Optional('pre_upload_script'): s.Str(),           # Script that will be configured to run in Theopenem's LIE imaging
                                                            # environment when uploading a disk image (at the BeforeImaging stage)
        s.Optional('image_info_script'): s.Str(),           # Script that will be configured to run in Theopenem's LIE imaging
                                                            # environment when deploying a disk image (at the AfterFileCopy stage)
        s.Optional('image_info_script_partition'):          # Partition on the disk image for which the `image_info_script` should
            s.Int(),                                        # run.
    }),

    s.Optional('login'): s.Map({
        'hint': s.Str(),                                    # login hint to show in the labManager master GUI
    }),

    s.Optional('clients'): s.Seq(                           # Configuration for known clients, e.g., fixed stations in a lab setup
        s.Map({
            'name': s.Str(),                                # Name by which station should be known. Example entry: STATION01
            'MAC' : s.CommaSeparated(s.Str()),              # One or multiple MAC addresses of the station. Example entry:
        })                                                  # 0C:9D:92:1F:E6:04, F4:E9:D4:73:6F:EC, F4:E9:D4:73:6F:ED
    ),

    s.Optional('tasks'): s.Seq(                             # preconfigured tasks to be shown in the labManager master GUI
        s.Map({
            'name': s.Str(),                                # Name to show in task GUI, should be descriptive
            'type': s.Enum(task.types),                     # Task type, one of labManager.common.task.Type
            s.Optional('payload', default=''): s.Str(),     # Command or other payload to execute
            s.Optional('payload_type', default='text'):     # Payload type, either as text to directly execute or path to a file to
                s.Enum(['text','file']),                    # load the payload from
            s.Optional('cwd', default=''): s.Str(),         # CWD in which to execute the command
            s.Optional('env', default=''): s.Str(),         # environment variables to set when executing the command
            s.Optional('interactive', default=False):       # Boolean indicating whether this is an interactive task. If true,
                s.Bool(),                                   # users can send commands to the task as it is running (e.g., use cmd
                                                            # as a remote shell)
            s.Optional('python_unbuffered', default=False): # if true, appends the -u flag to commands running the python
                s.Bool(),                                   # executable to put it in unbuffered mode, so that any output is
        }),                                                 # directly written to stdout/stderr and can be remotely monitored. Does
    ),                                                      # nothing for task types other than task.Type.Python_module and
                                                            # task.Type.Python_script
# end::master_schema[]
})
_default_master_config_file = 'master.yaml'
master = None

_client_schema = s.Map({
# tag::client_schema[]
    'network': s.Str(),                         # Network on which to discover clients, e.g. 10.0.1.0/24
    'network_retry': s.Map({                    # Configuration for retrying to get network connection on startup.
                                                # Useful whenit may take some time for the network connection to
                                                # come up after the computer station starts
        'number_tries': s.Int(),                # Number of times to try
        'wait': s.Int(),                        # Wait duration (s) between tries
    }),

    'service_discovery_protocol':               # Protocol to use for client discovery, MDNS or SSDP
        s.Enum(['MDNS','SSDP']),

    s.Optional('MDNS'): s.Map({
        'service': s.Str(),                     # Service name to discover when using MDNS, e.g.,
    }),                                         # _master._labManager._tcp.local.

    s.Optional('SSDP'): s.Map({
        'device_type': s.Str(),                 # Device type to announce and listen for when using SSDP, e.g.,
    }),                                         # urn:schemas-upnp-org:device:labManager
# end::client_schema[]
})
_default_client_config_file = 'client.yaml'
client = None

_admin_server_schema = s.Map({
# tag::admin-server_schema[]
    'LDAP': s.Map({                             # Configuration about the LDAP server and querying it
        'server': s.Str(),                      # Server FQDN or IP address
        'projects': s.Map({
            'format': s.Str(),                  # Regex to match projects in memberOf field of found user
        }),
    }),
    'toems': s.Map({                            # Configuration about the Theopenem instance
        'server': s.Str(),                      # Server FQDN or IP address
        'port': s.Int(),                        # Server port
        'images': s.Map({
            'format': s.Str(),                  # Regex to match disk images that belong to a project
            'file_copy_actions': s.Seq(         # List of file copy actions to activate when a new disk
                s.Map({                         # image is created
                    'name': s.Str(),            # Name in Theopenem of a file_copy action
                    'partition_id': s.Int(),    # Partition on the disk image for which the action should
                }),                             # be run
            ),
            'standard': s.Seq(                  # list of "standard" disk images that should be visible
                s.Str(),                        # (read only) to all projects. Use to, for instance,
            ),                                  # provide a base image that new projects can start from.
        }),
    }),
# end::admin-server_schema[]
})
_default_admin_server_config_file = 'admin_server.yaml'
admin_server = None

def load(which: str, file: str|pathlib.Path = None):
    global master, client, admin_server

    match which:
        case 'master':
            schema = _master_schema
            default_file = _default_master_config_file
        case 'client':
            schema = _client_schema
            default_file = _default_client_config_file
        case 'admin_server':
            schema = _admin_server_schema
            default_file = _default_admin_server_config_file
        case _:
            raise ValueError(f"which input argument '{which}' not recognized, should be one of 'master', 'client', 'admin_server'")

    if file is None:
        file = default_file

    with open(file,'rt') as f:
        config = s.load(f.read(), schema).data

    match which:
        case 'master':
            # extra validation and processing
            if config['service_discovery_protocol']=='MDNS' and 'MDNS' not in config:
                raise ValueError("If the key 'service_discovery_protocol' is set to 'MDNS', the 'MDNS' key is required, but it was not found")
            elif config['service_discovery_protocol']=='SSDP' and 'SSDP' not in config:
                raise ValueError("If the key 'service_discovery_protocol' is set to 'SSDP', the 'SSDP' key is required, but it was not found")
            if 'toems' in config and 'image_info_script' in config['toems'] and not 'image_info_script_partition' in config['toems']:
                raise ValueError("If toems.image_info_script is specified, toems.image_info_script_partition should also be specified")
            if 'tasks' in config:
                for i,t in enumerate(config['tasks']):
                    config['tasks'][i] = task.TaskDef.fromdict(t)
            master = config
        case 'client':
            # extra validation
            if config['service_discovery_protocol']=='MDNS' and 'MDNS' not in config:
                raise ValueError("If the key 'service_discovery_protocol' is set to 'MDNS', the 'MDNS' key is required, but it was not found")
            elif config['service_discovery_protocol']=='SSDP' and 'SSDP' not in config:
                raise ValueError("If the key 'service_discovery_protocol' is set to 'SSDP', the 'SSDP' key is required, but it was not found")
            client = config
        case 'admin_server':
            admin_server = config