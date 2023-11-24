import ldap3
import re
from .. import secrets

class LDAP_query:
    def __init__(self, server:str, username:str) -> None:
        self.server = server
        self.username = username
        self._serv = ldap3.Server(self.server, use_ssl=True, get_info=ldap3.ALL)

        # get user information
        conn = ldap3.Connection(self._serv, user=secrets.val['LDAP_ACCOUNT'], password=secrets.val['LDAP_PASSWORD'], auto_bind=True)
        conn.search(secrets.val['LDAP_SEARCH_BASE'], f'(samaccountname={username})', attributes=['displayName','memberOf'])
        self.user_info = conn.entries[0] if conn.entries else None
        conn.unbind()
        if not self.user_info:
            raise ValueError(f'user {username} unknown')

    def check_credentials(self, password:str):
        # Use distinguishedName to check user-provided credentials
        user_dn = self.user_info.entry_dn
        credentials_ok = False
        full_name = ''
        try:
            conn = ldap3.Connection(self._serv, user=user_dn, password=password, auto_bind=True)
        except ldap3.core.exceptions.LDAPBindError:
            pass    # credentials are not ok
        else:
            credentials_ok = True
            full_name = conn.extend.standard.who_am_i()
            if full_name.startswith('u:'):
                full_name = full_name[2:]
            conn.unbind()

        # format return
        if not credentials_ok:
            return {'success': False, 'error': f'credentials for user {self.username} incorrect'}
        return {'success': True, 'error': '', 'full_name': full_name, 'distinguished_name': user_dn}

    def get_group_memberships(self, project_format:str):
        # see what projects this user is a member of
        groups = {}
        proj_regex = re.compile(project_format)
        for g in self.user_info.memberOf:
            proj = proj_regex.findall(g)
            if len(proj)==1 and len(proj[0])==2:
                groups[proj[0][1]] = (proj[0][0],g)
        return groups
