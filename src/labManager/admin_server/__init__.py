from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import time
import copy
from dotenv import dotenv_values

from ..utils.network import smb
from ..utils.network import toems as toems_conn
from ..utils.network.ldap import check_credentials
from ..utils import config

# server with REST API for dealing with stuff that needs secret we don't want users to have access to:
# 1. LDAP querying for verifying user credentials and getting which projects they're members of
#    a. direct connection to LDAP system
#    b. optionally verified against SMB server
# 2. TOEMS user management through an admin account

app = FastAPI()


class Project(BaseModel):
    name: str
    full_name: str
    distinguished_name: str
    smb_access: int = -1    # -1: unknown, 0: no access, 1: access

def _next_id():
    if not users:
        return 1
    return max(users.keys()) + 1
class UserSession(BaseModel):
    name: str
    password: str   # only stored in memory, never written to disk
    full_name: str
    distinguished_name: str
    timestamp: float
    projects: list[Project] | None = None

class UserLogin(BaseModel):
    username: str
    password: str

class Image(BaseModel):
    name: str
    description: str | None

users = {}

class ToemsEntry(BaseModel):
    conn: toems_conn.Client
    group_id: int | None

    class Config:
        arbitrary_types_allowed = True

toems: dict[int, ToemsEntry] = {}


# 1a: LDAP / user credentials
@app.post('/users', status_code=201)
def user_add(user: UserLogin):
    # test login
    result = check_credentials(config.admin_server['LDAP']['server'], user.username, user.password, config.admin_server['LDAP']['projects']['format'])
    if not result['success']:
        raise HTTPException(status_code=401, detail=f'Login failed: {result["error"]}')

    # success, add new user
    # turn groups into project objects
    projects = [Project(name=k, full_name=result['groups'][k][0], distinguished_name=result['groups'][k][1]) for k in sorted(result['groups'])]
    # ID
    id = _next_id()
    # assemble user
    new_user = UserSession(name=user.username, password=user.password, full_name=result['full_username'], distinguished_name=result['distinguished_name'], timestamp=time.perf_counter(), projects=projects)
    # register user
    users[id] = new_user
    # return added user (password hidden)
    return {'id': id, 'user': return_user(new_user)}

@app.get('/users/{user_id}')
def user_detail(user_id: int):
    user_check(user_id)
    return {'user': return_user(users[user_id])}

def user_check(user_id):
    if not user_id in users:
        raise HTTPException(status_code=404, detail='User not found')

def return_user(user):
    # hides password
    reply_user = copy.deepcopy(user)
    reply_user.password = '***hidden***'
    return reply_user

# 1b. projects from LDAP
@app.get('/users/{user_id}/projects')
def user_projects(user_id: int):
    user_check(user_id)
    return users[user_id].projects

@app.get('/users/{user_id}/projects/{proj_id}')
def user_project_detail(user_id: int, proj_id: int):
    user_check(user_id)
    project_check(user_id, proj_id)
    return users[user_id].projects[proj_id]

def project_check(user_id, proj):
    if isinstance(proj, int):
        if proj<0 or proj>len(users[user_id].projects)-1:
            raise HTTPException(status_code=404, detail='Project not found')
    else:
        found = False
        for p in users[user_id].projects:
            if p.name==proj:
                found = True
                break
        if not found:
            raise HTTPException(status_code=404, detail='Project not found')

# 1c: SMB share access verification
@app.get('/users/{user_id}/projects/{proj_id}/check_smb')
def user_project_smb_check(user_id: int, proj_id: int):
    user_check(user_id)
    project_check(user_id, proj_id)
    # if we already know accessible state yet, connect to SMB server and query
    if users[user_id].projects[proj_id].smb_access == -1:  # -1 means not queried yet
        shares = SMB_get_shares(users[user_id])
        # set reported shares to reachable
        for s in shares:
            update_share_access(users[user_id].projects, s, 1)
        # set rest to unrechable
        for p in users[user_id].projects:
            if p.smb_access==-1:
                p.smb_access = 0
    return {'has_access': users[user_id].projects[proj_id].smb_access==1}

def SMB_get_shares(user):
    # figure out domain from user, default to configured
    domain = config.admin_server["SMB"]["domain"]
    if '\\' in user.full_name:
        dom, _ = user.full_name.split('\\',maxsplit=1)
        if dom:
            domain = dom
    try:
        smb_hndl = smb.SMBHandler(config.admin_server["SMB"]["server"], user.name, domain, user.password)
    except (OSError, smb.SessionError) as exc:
        print(f'Error connecting as {domain}\{user.name} to {config.master["SMB"]["server"]}: {exc}')
        shares = []
    else:
        shares = smb_hndl.list_shares(matching=config.admin_server["SMB"]["projects"]["format"], remove_trailing=config.admin_server["SMB"]["projects"]["remove_trailing"])

    return shares

def update_share_access(projects, share, state):
    for p in projects:
        if p.name==share:
            p.smb_access = state
            break

# 1d. user and user group management in TOEMS
@app.get('/users/{user_id}/projects/{proj_id}/toems')
async def user_toems_group(user_id: int, proj_id: int):
    user_check(user_id)
    project_check(user_id, proj_id)
    await toems_check(user_id)
    groups = await toems[user_id].conn.user_group_get()

    group_id = None
    for g in groups:
        if g['Name']==users[user_id].projects[proj_id].full_name:
            group_id = g['Id']

    if group_id is not None:
        toems[user_id].group_id = group_id

    return group_id is not None

@app.post('/users/{user_id}/projects/{proj_id}/toems_create', status_code=201)
async def user_toems_group_create(user_id: int, proj_id: int):
    user_check(user_id)
    project_check(user_id, proj_id)
    await toems_check(user_id)
    toems[user_id].group_id = await toems[user_id].conn.user_group_create(users[user_id].projects[proj_id].full_name, config.admin_server['toems']['images']['standard'])

async def toems_check(user_id):
    # create toems connection if needed
    if user_id not in toems:
        secrets = dotenv_values(".env")
        toems[user_id] = ToemsEntry(conn=toems_conn.Client(config.admin_server['toems']['server'], config.admin_server['toems']['port'], protocol='http'))
        await toems[user_id].conn.connect(username=secrets['TOEMS_ACCOUNT'], password=secrets['TOEMS_PASSWORD'])

# 1e. project image management
@app.post('/users/{user_id}/projects/{proj_id}/images', status_code=201)
async def user_toems_image_create(user_id: int, proj_id: int, image: Image):
    user_check(user_id)
    project_check(user_id, proj_id)
    await toems_check(user_id)
    resp = await toems[user_id].conn.image_create(image.name, project=users[user_id].projects[proj_id].name, project_format=config.admin_server['toems']['images']['format'], description=image.description)
    if not resp['Success']:
        if 'Already Exists' in resp['ErrorMessage']:
            raise HTTPException(status_code=409, detail=resp['ErrorMessage'])
        elif 'Authorized' in resp['ErrorMessage']:
            raise HTTPException(status_code=401, detail=resp['ErrorMessage'])
        else:
            raise HTTPException(status_code=400, detail=resp['ErrorMessage'])

    # set file copy actions for image
    image_id = resp['Id']
    if config.admin_server['toems']['images']['file_copy_actions']:
        resp = await toems[user_id].conn.image_set_file_copy_actions(image_id, config.admin_server['toems']['images']['file_copy_actions'])
        if not resp['Success']:
            raise HTTPException(status_code=400, detail=resp['ErrorMessage'])

    # make managed image for user group
    resp = await toems[user_id].conn.user_group_add_managed_images(toems[user_id].group_id, [image_id])
    if not resp['Success']:
        raise HTTPException(status_code=400, detail=resp['ErrorMessage'])

    # return id of created image
    return {'id': image_id}