from .registry import hooks


def register_module_hook(module_name, func=None):
    if func:
        hooks.register(module_name, func)
        return func

    def wrapper(func):
        return register_module_hook(module_name, func)
    return wrapper
