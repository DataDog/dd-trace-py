from importlib import import_module


class require_modules(object):
    """Context manager to check the availability of required modules"""

    def __init__(self, modules):
        self._missing_modules = []
        for module in modules:
            try:
                import_module(module)
            except ImportError:
                self._missing_modules.append(module)

    def __enter__(self):
        return self._missing_modules

    def __exit__(self, exc_type, exc_value, traceback):
        return False


def _resource_from_cache_prefix(resource, cache):
    """
    Combine the resource name with the cache prefix (if any) to generate
    the cache resource name
    """
    if getattr(cache, "key_prefix", None):
        name = "{} {}".format(resource, cache.key_prefix)
    else:
        name = resource

    # enforce lowercase to make the output nicer to read
    return name.lower()
