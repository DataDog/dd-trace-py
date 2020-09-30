from ddtrace import config

from .. import trace_utils

config._add("txredisapi", dict(_default_service="redis",))


def patch():
    import txredisapi

    pass


def unpatch():
    import txredisapi

    pass
