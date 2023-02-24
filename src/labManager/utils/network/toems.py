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
        resp = await self.user_group_add_managed_images(group_id, image_ids, overwrite=True)
        return group_id

    async def user_group_get_managed_images(self, group_id):
        return await self.request(f'/UserGroup/GetManagedImageIds/{group_id}')

    async def user_group_add_managed_images(self, group_id, image_ids, overwrite=False):
        ori_image_ids = []
        if not overwrite:
            ori_image_ids = await self.user_group_get_managed_images(group_id)
        return await self.request(f'/UserGroup/UpdateImageManagement/{group_id}', req_type='post', json=[{'UserGroupId': group_id, 'ImageId': i} for i in ori_image_ids+image_ids])


    async def computer_get(self, id=None, filter_list=None):
        if id:
            return await self.request(f'UserGroup/Get/{id}')
        else:
            comps = await self.request('/Computer/SearchAllComputers', req_type="post", json={'SearchText': "", 'Limit': "", 'CategoryType': "Any Category", 'State': "Any State", 'Status': "Any Status"})
            if filter_list:
                comps = [c for c in comps if c['Name'] in filter_list]
            return sorted(comps, key=lambda c: c['Name'])

    async def computer_deploy(self):
        pass

    async def computer_upload(self):
        # first check if image is protected. if so, cancel. erroring early here is nice.
        pass


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

    async def image_create(self, name, project, project_format, description=None):
        # check name
        r = re.compile(project_format)
        if not r.match(name):
            name = project + '_' + name

        # 1. create image
        resp = await self.request('Image/Post', req_type="post", json={
            "Description": description if description else "",
            "Enabled": True,
            "Environment": "linux",
            "IsVisible": True,
            "LastUploadGuid": None,
            "Name": name,
            "Protected": False,
            "Type": "Block"
        })
        # early exit if error
        if not resp['Success']:
            return {k.lower():resp[k] for k in resp}
        image_id = resp['Id']

        # return
        return {'success': True, 'id': image_id}

    async def image_change_protection(self, name_or_id, protect):
        # 1. get image id
        image_id = await self._image_resolve_id_name(name_or_id)

        # 2. get current image info
        image = await self.image_get(image_id)

        # 3. update it, changing protection status
        image['Protected'] = protect
        return await self.request(f'Image/Put/{image_id}', req_type="put", json=image)

    async def image_set_file_copy_actions(self, name_or_id, file_copy_actions):
        # 1. get image id
        image_id = await self._image_resolve_id_name(name_or_id)

        # 2. get all file copy actions (/FileCopyModule/Get)
        actions = await self.file_copy_actions_get()

        # 3. for image, set the image copy action (/ImageProfileFileCopy/Post)
        for i,act in enumerate(file_copy_actions):
            # get id of file copy action
            copy_id = None
            for fc in actions:
                if fc['Name']==act['name']:
                    copy_id = fc['Id']
                    break
            if not copy_id:
                raise ValueError(f'file copy action with name "{act["name"]}" not found')
            # activate file copy action for this image
            await self.request(f'/ImageProfileFileCopy/Post', req_type='post', json={
                "DestinationPartition": act['partition_id'],
                "FileCopyModuleId": copy_id,
                "Priority": i,
                "ProfileId": image_id
            })

    async def _image_resolve_id_name(self, name_or_id):
        image_id = None
        if isinstance(name_or_id, int):
            image_id = name_or_id
        else:
            images = await self.image_get()
            for im in images:
                # search both name and user-facing name
                if im['Name']==name_or_id or im['UserFacingName']==name_or_id:
                    image_id = im['Id']
                    break
            if not image_id:
                raise ValueError(f'image with name "{name_or_id}" not found')
        return image_id


    async def image_update(self, name, updates):
        # updates is a dict with items to be updated
        # 1. get current image info
        # 2. apply updates
        # 3. put (these three steps are needed as toems doesn't implement typical put handling)

        # refresh image cache
        pass

    async def image_delete(self):
        pass


    async def file_copy_actions_get(self, id=None):
        return await self.request('FileCopyModule/Get'+(f'/{id}' if id is not None else ''))