# module for interacting with Theopenem server
from authlib.integrations.httpx_client import AsyncOAuth2Client
from authlib.integrations.base_client.errors import InvalidTokenError
import re
import json
import shlex
import asyncio

class Client:
    def __init__(self, server, port=8080, protocol = 'https'):
        self.server     = server
        self.port       = port
        self.protocol   = protocol
        self._username  = None
        self._password  = None

        self.endpoint   = f'{self.protocol}://{self.server}:{self.port}/'

        self.client     = AsyncOAuth2Client()

    async def connect(self, username, password):
        self._username   = username
        self._password   = password
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

        try:
            resp = await coro
        except InvalidTokenError:
            # session expired, try to renew token and relaunch request
            await self.connect(self._username, self._password)
            return await self.request(resource, req_type, **kwargs)

        if resp.text:
            return resp.json()
        else:
            return None

    async def user_group_get(self, id=None):
        return await self.request('UserGroup/Get'+(f'/{id}' if id is not None else ''), req_type="post", json={'SearchText': "", 'Limit' : 0})

    async def user_group_create(self, name):
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
        return group_id

    async def user_group_get_managed_images(self, group_id):
        return await self.request(f'UserGroup/GetManagedImageIds/{group_id}')

    async def user_group_add_managed_images(self, group_id, image_ids, overwrite=False):
        ori_image_ids = []
        if not overwrite:
            ori_image_ids = await self.user_group_get_managed_images(group_id)
        coros = []
        for id in image_ids:
            coros.append(self._image_resolve_id_name(id))
        resps = await asyncio.gather(*coros)
        for resp in resps:
            if not resp['Success']:
                return resp
            else:
                if resp['Id'] not in ori_image_ids:
                    ori_image_ids.append(resp['Id'])
        return await self.request(f'UserGroup/UpdateImageManagement/{group_id}', req_type='post', json=[{'UserGroupId': group_id, 'ImageId': i} for i in ori_image_ids])


    async def computer_get(self, name_or_id=None, filter_list=None):
        if isinstance(name_or_id, int):
            return await self.request(f'Computer/Get/{name_or_id}')
        else:
            comps = await self.request('Computer/SearchAllComputers', req_type="post", json={'SearchText': "", 'Limit': "", 'CategoryType': "Any Category", 'State': "Any State", 'Status': "Any Status"})
            if filter_list:
                comps = [c for c in comps if c['Name'] in filter_list]
            if isinstance(name_or_id, str):
                found = False
                for c in comps:
                    if c['Name']==name_or_id:
                        return c
                if not found:
                    return None
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
            return {'Success': False, 'ErrorMessage': f'The image is empty.'}

        # 4. check that the selected computers have the correct image assigned
        coros = []
        for c in computer_ids:
            coros.append(self.computer_get(c))
        computers = await asyncio.gather(*coros)
        for computer in computers:
            if computer['ImageId'] != image_id:
                return {'Success': False, 'ErrorMessage': f'You do not have the right image (image_id should be: {image_id}, is: {computer["ImageId"]}) assigned to the computer {computer["Name"]} (computer_id: {c}).'}

        # 5. start deploy
        coros = []
        for c in computer_ids:
            coros.append(self.request(f'Computer/StartDeploy/{c}'))
        resps = await asyncio.gather(*coros)
        for resp in resps:
            if not 'Success' in resp['Value']:
                return {'Success': False, 'ErrorMessage': resp['Value']}

        return {'Success': True}

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

        # 4. check that the selected computers have the correct image assigned
        computer = await self.computer_get(computer_id)
        if computer['ImageId'] != image_id:
            return {'Success': False, 'ErrorMessage': f'You do not have the right image (image_id should be: {image_id}, is: {computer["ImageId"]}) assigned to the computer {computer["Name"]} (computer_id: {computer_id}).'}

        # 5. start upload
        resp = await self.request(f'Computer/StartUpload/{computer_id}')
        if 'Success' in resp['Value']:
            resp['Success'] = True
            return resp
        else:
            return {'Success': False, 'ErrorMessage': resp['Value']}


    async def image_get(self, name_or_id:str|int=None, project:str=None, project_format:str=None, name_mapping: dict=None):
        images = await self.request('Image/Get'+(f'/{name_or_id}' if isinstance(name_or_id, int) else ''))
        if isinstance(images, dict) and 'Success' in images and not images['Success']:
            # this only happens if an id is provided that doesn't exist or we do not have access to
            return None
        if isinstance(name_or_id, int):
            images = [images]

        if isinstance(name_or_id, str):
            found = False
            for im in images:
                if im['Name']==name_or_id:
                    images = [im]
                    found = True
                    break
            if not found:
                return None

        if project and project_format:
            # for images matching format, check they match project
            ims = []
            r = re.compile(project_format)
            for im in images:
                if (m := r.match(im['Name'])) is None or m.group()==project:
                    ims.append(im)
            images = ims

        for im in images:
            # check if image is part of the project (that is, starts with project name)
            # create a user-facing name, which does not include project name (if there was one)
            im['UserFacingName'] = im['Name']
            im['PartOfProject'] = project and im['Name'].startswith(project+'_')
            if im['PartOfProject']:
                im['UserFacingName'] = im['Name'][len(project)+1:]
            # globally override image name shown to user
            if name_mapping and im['Name'] in name_mapping:
                im['UserFacingName'] = name_mapping[im['Name']]

        if name_or_id is not None:
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
            image = await self.image_get(name_or_id)
            if image is not None:
                return {'Success': True, 'Id': image['Id']}
            return {'Success': False, 'ErrorMessage': f'image with name "{name_or_id}" not found. You may not have access to this image.'}

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

        # 2. get profile for this image
        profiles = await self.image_get_profiles(image_id)
        if len(profiles)==1:
            profile_id = profiles[0]['Id']
        else:
            return {'Success': False, 'ErrorMessage': 'image has more than one profile, cannot select profile to apply action to'}

        # 3. get all file copy actions
        actions = await self.file_copy_actions_get()

        # 4. for image, set the image copy action
        coros = []
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
            coros.append(self.request(f'ImageProfileFileCopy/Post', req_type='post', json={
                "DestinationPartition": act['partition_id'],
                "FileCopyModuleId": copy_id,
                "Priority": i,
                "ProfileId": profile_id
            }))
        resps = await asyncio.gather(*coros)
        for resp in resps:
            if not 'Success' in resp or not resp['Success']:
                # early exit upon error
                resp['Success'] = False # ensure this field exists, i have seen replies where it didn't...
                return resp
        return resp

    async def image_set_script_action(self, name_or_id, script_name, script_content, priority, run_when):
        # 1. get image id
        resp = await self._image_resolve_id_name(name_or_id)
        if not resp['Success']:
            return resp
        image_id = resp['Id']

        # 2. get profile for this image
        profiles = await self.image_get_profiles(image_id)
        if len(profiles)==1:
            profile_id = profiles[0]['Id']
        else:
            return {'Success': False, 'ErrorMessage': 'image has more than one profile, cannot select profile to apply action to'}

        # 3. find specified script action
        actions = await self.script_actions_get()
        script = None
        for act in actions:
            # search for script
            if act['Name']==script_name:
                script = act
                break
        if not script:
            return {'Success': False, 'ErrorMessage': f'script with name "{script_name}" not found'}

        # 4. update script to requested contents (skipped if empty)
        if script_content:
            script['ScriptContents'] = script_content
            script['ScriptType'] = 3 # 3: ImagingClient_Bash
            resp = await self.request(f'ScriptModule/Put/{script["Id"]}', req_type='put', json=script)
            if not 'Success' in resp or not resp['Success']:
                resp['Success'] = False # ensure this field exists and is filled, i have seen replies where it didn't...
                return resp

        # 5. update/activate/deactivate script action for this image
        resp = await self.request(f'ImageProfileScript/Post', req_type='post', json={
            "Priority": priority,
            "ProfileId": profile_id,
            "RunWhen": run_when,   # 0: Disabled, 1: BeforeImaging, 2: BeforeFileCopy, 3: AfterFileCopy
            "ScriptModuleId": script['Id']
        })
        if not 'Success' in resp or not resp['Success']:
            resp['Success'] = False # ensure this field exists and is filled, i have seen replies where it didn't...
        return resp

    async def image_get_profiles(self, name_or_id):
        # 1. get image id
        resp = await self._image_resolve_id_name(name_or_id)
        if not resp['Success']:
            return resp
        image_id = resp['Id']

        # 2. get image profiles
        return await self.request(f'Image/GetImageProfiles/{image_id}')

    async def image_get_server_size(self, name_or_id):
        # 1. get image id
        resp = await self._image_resolve_id_name(name_or_id)
        if not resp['Success']:
            return resp
        image_id = resp['Id']

        # 2. get current image info
        image = await self.image_get(image_id)

        # 3. get image size
        resp = await self.request('Image/GetImageSizeOnServer', params={'imageName': image['Name'], 'hdNumber':0})
        return resp['Value']

    async def image_get_audit_log(self, name_or_id):
        types = {   # https://github.com/jdolny/Toems/blob/master/Toems-Common/Enum/EnumAuditEntry.cs
            1: 'Create',
            2: 'Update',
            3: 'Archive',
            4: 'Delete',
            5: 'ActivatePolicy',
            6: 'DeactivatePolicy',
            7: 'ApproveProvision',
            8: 'ApproveReset',
            9: 'AddPreProvision',
            10: 'SuccessfulLogin',
            11: 'FailedLogin',

            13: 'Message',
            14: 'Reboot',
            15: 'Shutdown',
            16: 'Wakeup',
            17: 'Restore',
            18: 'OnDemandMulticast',
            19: 'Multicast',
            20: 'Deploy',
            21: 'Upload',
            22: 'OndUpload',
            23: 'OndDeploy',
            24: 'RemoteControl'
        }
        # 1. get image id
        resp = await self._image_resolve_id_name(name_or_id)
        if not resp['Success']:
            return resp
        image_id = resp['Id']

        # 2. get image audit logs
        resp = await self.request(f'Image/GetImageAuditLogs/{image_id}', params={'limit':9999})
        # 3. map type id to something we understand
        for d in resp:
            d['AuditType'] = types[d['AuditType']] if d['AuditType'] in types else 'Unknown'
        return resp

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


    async def imaging_tasks_get_active(self):
        resp = await self.request('ActiveImagingTask/GetActiveTasks')
        for r in resp:
            # legend: status TaskCreated = 0, WaitingForLogin = 1, CheckedIn = 2, InImagingQueue = 3, Imaging = 4
            # https://github.com/jdolny/Toems/blob/master/Toems-Common/Enum/EnumTaskStatus.cs
            match r['Status']:
                case 0:
                    r['Status'] = 'TaskCreated'
                case 1:
                    r['Status'] = 'WaitingForLogin'
                case 2:
                    r['Status'] = 'CheckedIn'
                case 3:
                    r['Status'] = f'InQueue (Position {r["QueuePosition"]})'
                case 4:
                    r['Status'] = 'Imaging'
        return resp

    async def imaging_tasks_cancel_active(self, task_id: int):
        return await self.request(f'ActiveImagingTask/Delete/{task_id}', req_type='delete')


    async def file_copy_actions_get(self, id=None):
        return await self.request('FileCopyModule/Get'+(f'/{id}' if id is not None else ''))

    async def script_actions_get(self, id=None):
        return await self.request('ScriptModule/Get'+(f'/{id}' if id is not None else ''))

# helpers
def make_info_script(info, partition):
    info = shlex.quote(json.dumps(info))
    script = f"""
mkdir /mnt/win &>/dev/null
ntfs-3g ${{hard_drive}}${{partition_prefix}}{partition} /mnt/win
echo {info} > /mnt/win/image_info.json
umount ${{hard_drive}}${{partition_prefix}}{partition}
"""
    return script