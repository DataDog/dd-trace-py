"""
TODO
"""
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w
from ddtrace.compat import string_type
from ...pin import Pin
from ...utils.import_hook import install_module_import_hook


__all__ = [
    'patch',
]


def with_instance_pin(func):
    """Helper to wrap a function wrapper and ensure an enabled pin is available for the `instance`"""
    def wrapper(wrapped, instance, args, kwargs):
        pin = Pin._find(wrapped, instance)
        if not pin or not pin.enabled():
            return wrapped(*args, **kwargs)

        return func(pin, wrapped, instance, args, kwargs)
    return wrapper


def unpatch():
    pass


@with_instance_pin
def traced_enqueue(pin, func, instance, args, kwargs):
    # According to rq `f` can be:
    #  * A reference to a function
    #  * A reference to an object's instance method
    #  * A string, representing the location of a function (must be
    #    meaningful to the import context of the workers)
    f = args[1]

    print('wtf')

    if isinstance(f, string_type):
        pass

    print(args)
    return func(*args, **kwargs)


def _patch_rq(rq):
    print('\n\n\n\nWTF\n\n\n\n\n')
    _w('rq', 'Queue.enqueue', traced_enqueue)


def patch():
    print('TEST')
    install_module_import_hook('rq', _patch_rq)

