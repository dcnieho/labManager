# module for interacting with Theopenem server
from authlib.integrations.httpx_client import AsyncOAuth2Client
import re

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
        resp = await self.request('/UserGroupRight/Post', req_type='post', json=[{'UserGroupId': group_id, 'Right': r} for r in ["computerRead","imageRead","imageUploadTask","imageDeployTask"]])
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
        resp = await self.request(f'/UserGroup/UpdateImageManagement/{group_id}', req_type='post', json=[{'UserGroupId': group_id, 'ImageId': i} for i in image_ids])


    async def computer_get(self, id=None, filter_list=None):
        if id:
            return await self.request(f'UserGroup/Get/{id}')
        else:
            comps = await self.request('/Computer/SearchAllComputers', req_type="post", json={'SearchText': "", 'Limit': "", 'CategoryType': "Any Category", 'State': "Any State", 'Status': "Any Status"})
            if filter_list:
                comps = [c for c in comps if c['Name'] in filter_list]
            return sorted(comps, key=lambda c: c['Name'])


    async def image_get(self, id=None, project=None, project_format=None):
        images = await self.request('Image/Get'+(f'/{id}' if id is not None else ''))
        if id is not None:
            images = [images]

        if project and project_format:
            # for images matching format, check they match project
            ims = []
            r = re.compile(project_format)
            for im in images:
                if (m := r.match(im['Name'])) is None or m.group()==project:
                    ims.append(im)
            images = ims

        for im in images:
            # add user-facing name, which does not include project name (if there was one)
            im['UserFacingName'] = im['Name']
            if project and im['Name'].startswith(project+'_'):
                im['UserFacingName'] = im['Name'][len(project)+1:]

        if id is not None:
            if images:
                images = images[0]
            else:
                images = None
        return images