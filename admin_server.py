from labManager import admin_server
app = admin_server.app

if __name__ == "__main__":
    raise RuntimeError('run by means of calling "uvicorn admin_server:app"')