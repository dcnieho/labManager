from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import time

from ..utils.network.ldap import check_credentials
from ..utils import config

# server with REST API for dealing with stuff that needs secret we don't want users to have access to:
# 1. LDAP querying for verifying user credentials and getting which projects they're members of
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
class User(BaseModel):
    name: str
    full_name: str
    distinguished_name: str
    timestamp: float
    projects: list[Project] | None = None

class UserLogin(BaseModel):
    name: str
    password: str

users = {}


@app.get('/users/{user_id}')
def user_detail(user_id: int):
    user_check(user_id)
    return {'user': users[user_id]}

@app.get('/users/{user_id}/projects')
def user_detail(user_id: int):
    user_check(user_id)
    return users[user_id].projects

@app.get('/users/{user_id}/projects/{proj_id}')
def user_detail(user_id: int, proj_id: int):
    user_check(user_id)
    return users[user_id].projects[proj_id]

@app.post('/users')
def user_add(user: UserLogin):
    # test login
    result = check_credentials(config.admin_server['server'], user.name, user.password)
    if not result['success']:
        raise HTTPException(status_code=401, detail=f'Login failed: {result["error"]}')

    # success, add new user
    # turn groups into project objects
    projects = [Project(name=k, full_name=result['groups'][k][0], distinguished_name=result['groups'][k][1]) for k in result['groups']]
    # ID
    id = _next_id()
    # assemble user
    new_user = User(name=user.name, full_name=result['full_username'], distinguished_name=result['distinguished_name'], timestamp=time.perf_counter(), projects=projects)
    # register user
    users[id] = new_user
    # return added user
    return {'id': id, 'user': new_user}

def user_check(user_id):
    if not user_id in users:
        raise HTTPException(status_code=404, detail='User Not Found')