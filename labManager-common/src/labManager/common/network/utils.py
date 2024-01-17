def get_domain_username(user: str, default_domain: str):
    # figure out domain from user (format domain\user)
    # if no domain found in user string, use default_domain
    domain = default_domain
    if '\\' in user:
        dom, user = user.split('\\', maxsplit=1)
        if dom:
            domain = dom
    return domain, user