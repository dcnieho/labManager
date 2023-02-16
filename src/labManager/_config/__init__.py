HAS_MASTER = False
HAS_ADMIN  = False

# test if the master dependencies are available
# if not, user didn't specify the "master" extra
# if they are, no harm ;)
try:
    import authlib as al
except:
    pass
else:
    del al

    try:
        import httpx as h
    except:
        pass
    else:
        del h
        HAS_MASTER = True


# test if the admin server dependencies are available
try:
    import dotenv as de
except:
    pass
else:
    del de