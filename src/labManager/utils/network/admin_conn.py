import httpx
import json

class Client:
    def __init__(self, server, port, protocol = 'http'):
        self.server     = server
        self.port       = port
        self.protocol   = protocol

        self.endpoint   = f'{self.protocol}://{self.server}:{self.port}/'

        self.client     = httpx.AsyncClient()

        self.user_id    = None
        self.user       = None
        self.proj_id    = None

    async def request(self, resource, req_type='get', expected_return_code=200, **kwargs):
        url = self.endpoint+resource
        match req_type:
            case 'get':
                coro = self.client.get   (url, **kwargs)
            case 'post':
                coro = self.client.post  (url, **kwargs)
            case 'put':
                coro = self.client.put   (url, **kwargs)
            case 'delete':
                coro = self.client.delete(url, **kwargs)
            case _:
                raise ValueError

        resp = (await coro)
        if not resp.status_code==expected_return_code:
            raise RuntimeError(f'Login failed: {resp.status_code}: {resp.reason_phrase}\n{resp.text}')
        return resp.json()

    async def login(self, user, password):
        resp = await self.request('users', req_type='post', data=json.dumps({'username':user, 'password':password}))
        self.user_id = resp['id']
        self.user = resp['user']

        return [p['name'] for p in self.user['projects']]

    def set_project(self, project):
        self.proj_id = self._get_project_id(project)
        if self.proj_id is None:
            raise RuntimeError(f'project "{project}" not recognized, choose one of the projects you have access to: {[p["name"] for p in self.user["projects"]]}')

    def _get_project_id(self, project):
        if self.user is None:
            raise RuntimeError('You need to successfully log in before setting a project')

        # check if this is a project
        proj_id = None
        for i,p in enumerate(self.user['projects']):
            if p['name']==project:
                proj_id = i
                break
        return proj_id

    async def check_share_access(self, proj=None):
        if self.user is None:
            raise RuntimeError('You need to successfully log in before setting a project')

        proj_id = self.proj_id
        if proj is not None:
            proj_id = self._get_project_id(proj)
        if proj_id is None:
            raise RuntimeError('You need to set a project before checking its details')

        resp = await self.request(f'users/{self.user_id}/projects/{proj_id}/check_smb')
        return resp['has_access']