import ldap3
import re
from .. import secrets


def check_credentials(server:str, username:str, password:str, project_format:str = None):
    serv = ldap3.Server(server, use_ssl=True, get_info=ldap3.ALL)
    conn = ldap3.Connection(serv, user=secrets.val['LDAP_ACCOUNT'], password=secrets.val['LDAP_PASSWORD'], auto_bind=True)

    conn.search('OU=People,DC=uw,DC=lu,DC=se', f'(samaccountname={username})', attributes=['displayName','memberOf'])
    results = conn.entries
    if not results:
        return {'success': False, 'error': f'user {username} unknown'}

    user_dn = results[0].entry_dn
    # 1.a: see what projects this user is a member of
    groups = {}
    if project_format:
        proj_regex = re.compile(project_format)
        for g in results[0].memberOf:
            proj = proj_regex.findall(g)
            if len(proj)==1 and len(proj[0])==2:
                groups[proj[0][1]] = (proj[0][0],g)
    conn.unbind()


    # 2. use distinguishedName to check user-provided credentials
    credentials_ok = False
    full_name = ''
    try:
        conn = ldap3.Connection(serv, user=user_dn, password=password, auto_bind=True)
    except ldap3.core.exceptions.LDAPBindError:
        pass    # credentials are not ok
    else:
        credentials_ok = True
        full_name = conn.extend.standard.who_am_i()
        if full_name.startswith('u:'):
            full_name = full_name[2:]
    conn.unbind()

    # 3. format return
    if not credentials_ok:
        return {'success': False, 'error': f'credentials for user {username} incorrect'}

    return {'success': True, 'error': '', 'full_username': full_name, 'distinguished_name': user_dn, 'groups': groups}