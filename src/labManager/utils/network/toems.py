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
        resp = await self.request('UserGroupRight/Post', req_type='post', json=[{'UserGroupId': group_id, 'Right': r} for r in ["computerRead","imageRead","imageUploadTask","imageDeployTask"]])
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
        return await self.request(f'UserGroup/GetManagedImageIds/{group_id}')

    async def user_group_add_managed_images(self, group_id, image_ids, overwrite=False):
        ori_image_ids = []
        if not overwrite:
            ori_image_ids = await self.user_group_get_managed_images(group_id)
        return await self.request(f'UserGroup/UpdateImageManagement/{group_id}', req_type='post', json=[{'UserGroupId': group_id, 'ImageId': i} for i in ori_image_ids+image_ids])


    async def computer_get(self, id=None, filter_list=None):
        if id:
            return await self.request(f'Computer/Get/{id}')
        else:
            comps = await self.request('Computer/SearchAllComputers', req_type="post", json={'SearchText': "", 'Limit': "", 'CategoryType': "Any Category", 'State': "Any State", 'Status': "Any Status"})
            if filter_list:
                comps = [c for c in comps if c['Name'] in filter_list]
            return sorted(comps, key=lambda c: c['Name'])

    async def _computer_resolve_id_name(self, name_or_id):
        is_single = not isinstance(name_or_id, list)
        if is_single:
            name_or_id = [name_or_id]

        computers = None
        out = []
        for ni in name_or_id:
            if isinstance(ni, int):
                out.append(ni)
            else:
                if computers == None:
                    computers = await self.computer_get()
                found = False
                for c in computers:
                    if c['Name']==ni:
                        out.append(c['Id'])
                        found = True
                        break
                if not found:
                    return {'Success': False, 'ErrorMessage': f'computer with name "{name_or_id}" not found'}

        if is_single:
            return {'Success': True, 'Id': out[0]}
        else:
            return {'Success': True, 'Ids': out}

    async def computer_update(self, name_or_id, updates):
        # 1. get computer id
        resp = await self._computer_resolve_id_name(name_or_id)
        if not resp['Success']:
            return resp
        computer_id = resp['Id']

        # 2. get current computer info
        computer = await self.computer_get(computer_id)

        # 3. apply updates
        for u in updates:
            computer[u] = updates[u]

        # 4. update the image (server doesn't partial updates, hence these steps)
        return await self.request(f'Computer/Put/{computer_id}', req_type="put", json=computer)

    async def computer_deploy(self, image_name_or_id, computer_names_or_ids):
        # 1. get computer ids
        if not isinstance(computer_names_or_ids,list):
            computer_names_or_ids = [computer_names_or_ids]
        resp = await self._computer_resolve_id_name(computer_names_or_ids)
        if not resp['Success']:
            return resp
        computer_ids = resp['Ids']

        # 2. get image id
        resp = await self._image_resolve_id_name(image_name_or_id)
        if not resp['Success']:
            return resp
        image_id = resp['Id']

        # 3. check image has a size (if no image it can't be deployed)
        size = await self.image_get_server_size(image_id)
        if size=='N/A':
            return {'Success': False}

        # 4. update the selected computers so that the correct image is assigned
        for c in computer_ids:
            resp = await self.computer_update(c,{'ImageId': image_id})
            if not resp['Success']:
                return resp

        # 5. start deploy
        for c in computer_ids:
            resp = await self.request(f'Computer/StartDeploy/{c}')
            if not 'Success' in resp['Value']:
                return {'Success': False, 'ErrorMessage': resp['Value']}

        return resp

    async def computer_upload(self, computer_name_or_id, image_name_or_id):
        # 1. get image id
        resp = await self._image_resolve_id_name(image_name_or_id)
        if not resp['Success']:
            return resp
        image_id = resp['Id']

        # 2. get current image info and check if image is protected. if so, error
        image = await self.image_get(image_id)
        if image['Protected']:
            return {'Success': False, 'ErrorMessage': 'Cannot upload computer to a protected image. Unprotect the image first.'}

        # 3. get computer id
        resp = await self._computer_resolve_id_name(computer_name_or_id)
        if not resp['Success']:
            return resp
        computer_id = resp['Id']

        # 4. update the selected computer so that the correct image is assigned
        resp = await self.computer_update(computer_id,{'ImageId': image_id})
        if not resp['Success']:
            return resp

        # 5. start deploy
        resp = await self.request(f'Computer/StartUpload/{computer_id}')
        if not 'Success' in resp['Value']:
            return {'Success': False, 'ErrorMessage': resp['Value']}
        return resp


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
        else:
            images = sorted(images, key=lambda c: c['UserFacingName'])
        return images

    async def _image_resolve_id_name(self, name_or_id):
        if isinstance(name_or_id, int):
            return {'Success': True, 'Id': name_or_id}
        else:
            images = await self.image_get()
            for im in images:
                # search both name and user-facing name
                if im['Name']==name_or_id or im['UserFacingName']==name_or_id:
                    return {'Success': True, 'Id': im['Id']}
            return {'Success': False, 'ErrorMessage': f'image with name "{name_or_id}" not found'}

    async def image_create(self, name, project, project_format, description=None):
        # check name
        r = re.compile(project_format)
        if not r.match(name):
            name = project + '_' + name

        # 1. create image
        return await self.request('Image/Post', req_type="post", json={
            "Description": description if description else "",
            "Enabled": True,
            "Environment": "linux",
            "IsVisible": True,
            "LastUploadGuid": None,
            "Name": name,
            "Protected": False,
            "Type": "Block"
        })

    async def image_set_file_copy_actions(self, name_or_id, file_copy_actions):
        # 1. get image id
        resp = await self._image_resolve_id_name(name_or_id)
        if not resp['Success']:
            return resp
        image_id = resp['Id']

        # 2. get all file copy actions
        actions = await self.file_copy_actions_get()

        # 3. for image, set the image copy action
        for i,act in enumerate(file_copy_actions):
            # get id of file copy action
            copy_id = None
            for fc in actions:
                if fc['Name']==act['name']:
                    copy_id = fc['Id']
                    break
            if not copy_id:
                return {'Success': False, 'ErrorMessage': f'file copy action with name "{act["name"]}" not found'}
            # activate file copy action for this image
            resp = await self.request(f'ImageProfileFileCopy/Post', req_type='post', json={
                "DestinationPartition": act['partition_id'],
                "FileCopyModuleId": copy_id,
                "Priority": i,
                "ProfileId": image_id
            })
            if not resp['Success']:
                # early exit upon error
                return resp
        return resp

    async def image_get_server_size(self, name_or_id):
        # 1. get image id
        resp = await self._image_resolve_id_name(name_or_id)
        if not resp['Success']:
            return resp
        image_id = resp['Id']

        # 2. get current image info
        image = await self.image_get(image_id)

        # 3. get image size
        resp = await self.request('Image/GetImageSizeOnServer', data={'imageName': image['Name'], 'hdNumber':0})
        return resp['Value']

    async def image_update(self, name_or_id, updates):
        # 1. get image id
        resp = await self._image_resolve_id_name(name_or_id)
        if not resp['Success']:
            return resp
        image_id = resp['Id']

        # 2. get current image info
        image = await self.image_get(image_id)

        # 3. apply updates
        for u in updates:
            image[u] = updates[u]

        # 4. update the image (server doesn't partial updates, hence these steps)
        return await self.request(f'Image/Put/{image_id}', req_type="put", json=image)

    async def image_delete(self, name_or_id):
        # 1. get image id
        resp = await self._image_resolve_id_name(name_or_id)
        if not resp['Success']:
            return resp
        image_id = resp['Id']

        # 2. delete
        return await self.request(f'Image/Delete/{image_id}', req_type='delete')


    async def imaging_task_get(self):
        return await self.request('ActiveImagingTask/GetActiveTasks')


    async def file_copy_actions_get(self, id=None):
        return await self.request('FileCopyModule/Get'+(f'/{id}' if id is not None else ''))