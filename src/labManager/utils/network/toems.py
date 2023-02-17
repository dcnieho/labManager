# module for interacting with Theopenem server
from authlib.integrations.httpx_client import AsyncOAuth2Client

class Client:
    def __init__(self, server, port=8080, protocol = 'https'):
        self.server     = server
        self.port       = port
        self.protocol   = protocol

        self.endpoint   = f'{self.protocol}://{self.server}:{self.port}/'

        self.client     = AsyncOAuth2Client()

    async def connect(self, username, password):
        self.token = await self.client.fetch_token(
            self.endpoint+'token',
            grant_type='password',
            username=username,
            password=password
        )


    async def request(self, resource, req_type='get', **kwargs):
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
        return (await coro).json()

    async def user_group_get(self, id=None):
        return await self.request('UserGroup/Get'+(f'/{id}' if id is not None else ''), req_type="post", json={'SearchText': "", 'Limit' : 0})

    async def user_group_create(self, name, images):
        # 1. create group
        resp = await self.request('UserGroup/Post', req_type="post", json={
            "Name": name,
            "Membership": "User",
            "IsLdapGroup": 1,
            "GroupLdapName": name,
            "EnableImageAcls": True,
            "EnableComputerGroupAcls": False
        })
        group_id = resp['Id']
        # 2. set ACLs
        resp = await self.request('/UserGroupRight/Post',req_type='post',json=[{'UserGroupId':group_id, 'Right': r} for r in ["groupRead","computerRead","imageRead","imageUpdate","imageDelete","imageUploadTask","imageDeployTask"]])
        # 3. provide access to images
        # 3a. for the named images, find out what the image ids are
        image_ids = []
        resp = await self.image_get()
        for im in images:
            for ims in resp:
                if ims['Name']==im:
                    image_ids.append(ims['Id'])
                    break
        # 3b. set access to these images
        resp = await self.request(f'/UserGroup/UpdateImageManagement/{group_id}', req_type='post',json=[{'UserGroupId':group_id, 'ImageId': i} for i in image_ids])


    async def image_get(self, id=None, project=''):
        images = await self.request('Image/Get'+(f'/{id}' if id is not None else ''))
        if id is not None:
            images = [images]

        if project:
            # only list images for a specific project, those have name starting with <project>_
            images = [im for im in images if im['Name'].startswith(project+'_')]

            # add user-facing name, which does not include project name (if there was one)
            for im in images:
                im['UserFacingName'] = im['Name'][len(project)+1:]

        if id is not None:
            if images:
                images = images[0]
            else:
                images = None
        return images