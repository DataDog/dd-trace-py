BLACKLIST_ENDPOINT = [
    'kms',
    'sts']

def is_blacklist(endpoint_name):
    """Protecting the args sent to kms, sts to avoid security leaks
    """
    if endpoint_name in BLACKLIST_ENDPOINT:
        return True
    else:
        return False
