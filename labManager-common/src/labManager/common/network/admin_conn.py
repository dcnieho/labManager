import httpx

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
            case 'patch':
                coro = self.client.patch (url, **kwargs)
            case 'delete':
                coro = self.client.delete(url, **kwargs)
            case _:
                raise ValueError

        resp = await coro
        if not resp.status_code==expected_return_code:
            raise RuntimeError(f'Action failed: {resp.status_code}: {resp.reason_phrase}\n{resp.text}')
        if resp.text:
            return resp.json()
        else:
            return None

    async def login(self, user, password):
        resp = await self.request('users', req_type='post', json={'username':user, 'password':password}, expected_return_code=201)
        self.user_id = resp['id']
        self.user = resp['user']

    def get_projects(self):
        self._check_user()
        return [p['name'] for p in self.user['projects']]

    def set_project(self, project):
        self.proj_id = self._get_project_id(project)
        if self.proj_id is None:
            raise RuntimeError(f'project "{project}" not recognized, choose one of the projects you have access to: {[p["name"] for p in self.user["projects"]]}')

    def unset_project(self):
        self.proj_id = None

    async def prep_toems(self):
        # check there is a user group for the selected project. If not, make one
        self._check_user()
        self._check_project()

        group_exists = await self.request(f'users/{self.user_id}/projects/{self.proj_id}/toems')
        if not group_exists:
            group_exists = await self.request(f'users/{self.user_id}/projects/{self.proj_id}/toems', req_type='post', expected_return_code=204)

    def _check_user(self):
        if self.user is None:
            raise RuntimeError('You need to successfully log in first')

    def _check_project(self):
        if self.proj_id is None:
            raise RuntimeError('You need to set a project first')

    def _get_project_id(self, project):
        self._check_user()

        # check if this is a project
        proj_id = None
        for i,p in enumerate(self.user['projects']):
            if p['name']==project:
                proj_id = i
                break
        return proj_id

    async def create_image(self, name, description=None):
        resp = await self.request(f'users/{self.user_id}/projects/{self.proj_id}/images', req_type='post', json={'name':name, 'description':description}, expected_return_code=201)
        return resp['Id']

    async def update_image(self, image_id, updates):
        return await self.request(f'users/{self.user_id}/projects/{self.proj_id}/images/{image_id}', req_type='put', json=updates)

    async def delete_image(self, image_id):
        await self.request(f'users/{self.user_id}/projects/{self.proj_id}/images/{image_id}', req_type='delete', expected_return_code=204)

    async def apply_image(self, image_id, computer_id):
        return await self.request(f'users/{self.user_id}/projects/{self.proj_id}/images/{image_id}/apply', req_type='post', json={'computer_id': computer_id})
