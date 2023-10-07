from fastapi import FastAPI, HTTPException
from typing import Optional
from pydantic import ConfigDict, BaseModel
import time
import copy

from labManager.common.network import ldap
from labManager.common.network import toems as toems_conn
from labManager.common import config, secrets

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
    projects: Optional[list[Project]] = None

class UserLogin(BaseModel):
    username: str
    password: str

class Image(BaseModel):
    name: str
    description: Optional[str] = None

users = {}

class ToemsEntry(BaseModel):
    conn: toems_conn.Client
    group_id: Optional[int] = None
    model_config = ConfigDict(arbitrary_types_allowed=True)

toems: dict[int, ToemsEntry] = {}


# 1: LDAP / user credentials
@app.post('/users', status_code=201)
def user_add(user: UserLogin):
    # test login
    result = ldap.check_credentials(config.admin_server['LDAP']['server'], user.username, user.password, config.admin_server['LDAP']['projects']['format'])
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

@app.delete('/users/{user_id}', status_code=204)
def user_delete(user_id: int):
    if user_id in users:
        del users[user_id]
    if user_id in toems:
        del toems[user_id]

def user_check(user_id):
    if not user_id in users:
        raise HTTPException(status_code=404, detail='User not found')

def return_user(user):
    # hides password
    reply_user = copy.deepcopy(user)
    reply_user.password = '***hidden***'
    return reply_user

# 2. projects from LDAP
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

# 3. user and user group management in TOEMS
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

@app.post('/users/{user_id}/projects/{proj_id}/toems', status_code=204)
async def user_toems_group_create(user_id: int, proj_id: int):
    user_check(user_id)
    project_check(user_id, proj_id)
    await toems_check(user_id)
    toems[user_id].group_id = await toems[user_id].conn.user_group_create(users[user_id].projects[proj_id].full_name, config.admin_server['toems']['images']['standard'])

async def toems_check(user_id):
    # create toems connection if needed
    if user_id not in toems:
        toems[user_id] = ToemsEntry(conn=toems_conn.Client(config.admin_server['toems']['server'], config.admin_server['toems']['port'], protocol='http'))
        await toems[user_id].conn.connect(username=secrets.val['TOEMS_ACCOUNT'], password=secrets.val['TOEMS_PASSWORD'])

# 4. project image management in TOEMS
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

    # return created image (make sure fully in sync with results of all above calls)
    return await toems[user_id].conn.image_get(image_id)

@app.put('/users/{user_id}/projects/{proj_id}/images/{image_id}')
async def user_toems_image_update(user_id: int, proj_id: int, image_id: int, updates: dict):
    user_check(user_id)
    project_check(user_id, proj_id)
    await toems_check(user_id)
    # 1. first check this image belongs to the user's project (by means of name)
    image = await _toems_get_image(toems[user_id].conn, image_id)
    if not image['Name'].startswith(users[user_id].projects[proj_id].name+'_'):
        raise HTTPException(status_code=403, detail=f'You are not allowed to update the image "{image["Name"]}" because it is not a part of your project.')

    # 2. then, apply updates
    resp = await toems[user_id].conn.image_update(image_id, updates)
    if not resp['Success']:
        raise HTTPException(status_code=400, detail=resp['ErrorMessage'])

    # 3. return updated entry
    return await toems[user_id].conn.image_get(image_id)

@app.delete('/users/{user_id}/projects/{proj_id}/images/{image_id}', status_code=204)
async def user_toems_image_delete(user_id: int, proj_id: int, image_id: int):
    user_check(user_id)
    project_check(user_id, proj_id)
    await toems_check(user_id)
    # 1. first check this image belongs to the user's project (by means of name)
    image = await _toems_get_image(toems[user_id].conn, image_id)
    if not image['Name'].startswith(users[user_id].projects[proj_id].name+'_'):
        raise HTTPException(status_code=403, detail=f'You are not allowed to delete the image "{image["Name"]}" because it is not a part of your project.')

    # 2. then, delete
    resp = await toems[user_id].conn.image_delete(image_id)
    if not resp['Success']:
        raise HTTPException(status_code=400, detail=resp['ErrorMessage'])

async def _toems_get_image(toems_conn, image_id):
    # create toems connection if needed
    image = await toems_conn.image_get(image_id)
    if not image:
        raise HTTPException(status_code=404, detail=f'No image with id {image_id}.')
    return image

@app.post('/users/{user_id}/projects/{proj_id}/images/{image_id}/apply')
async def user_toems_image_apply(user_id: int, proj_id: int, image_id: int, computer_id: dict):
    user_check(user_id)
    project_check(user_id, proj_id)
    await toems_check(user_id)

    # check image exists
    image = await _toems_get_image(toems[user_id].conn, image_id)

    # check image profile for this image
    profiles = await toems[user_id].conn.image_get_profiles(image_id)
    if len(profiles)==1:
        profile_id = profiles[0]['Id']
    else:
        return {'Success': False, 'ErrorMessage': 'image has more than one profile, cannot select profile'}

    # update computer with image
    return await toems[user_id].conn.computer_update(computer_id['computer_id'], {'ImageId': image['Id'], 'ImageProfileId': profile_id})