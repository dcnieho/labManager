# module for interacting with Theopenem server


from authlib.integrations.httpx_client import AsyncOAuth2Client

class Client:
    def __init__(self, server, port=8080, protocol = 'https'):
        self.server     = server
        self.port       = port
        self.protocol   = protocol

        self.endpoint   = f'{self.protocol}://{self.server}:{self.port}/'

        self.client = AsyncOAuth2Client()

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

    async def image_get(self, id=None):
        return await self.request('Image/Get'+(f'/{id}' if id else ''))