from copy import deepcopy

# https://stackoverflow.com/a/7205107
def merge(a, b, path=None):
    """merges b into a"""
    if path is None:
        path = []
    for key in b:
        if key in a:
            if isinstance(a[key], dict) and isinstance(b[key], dict):
                merge(a[key], b[key], path + [str(key)])
            elif a[key] == b[key]:
                pass # same leaf value
            else:
                a[key] = b[key]
        else:
            a[key] = b[key]
    return a


def override_config(custom_conf):
    """Overrides the vertica configuration and reinstalls the previous
    afterwards."""
    from ddtrace import config

    def provide_config(func):
        def wrapper(*args, **kwargs):
            orig = deepcopy(config.vertica)
            merge(config.vertica, custom_conf)
            r = func(*args, **kwargs)
            config._add('vertica', orig)
            return r
        return wrapper
    return provide_config
