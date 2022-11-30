from ddtrace.vendor import wrapt


def unwrap(obj, attr):
    f = getattr(obj, attr, None)
    if f and isinstance(f, wrapt.ObjectProxy) and hasattr(f, "__wrapped__"):
        setattr(obj, attr, f.__wrapped__)


def get_root_wrapped(obj):
    while isinstance(obj, _wrapt_objproxy_types) and hasattr(obj, "__wrapped__"):
        obj = obj.__wrapped__

    return obj
