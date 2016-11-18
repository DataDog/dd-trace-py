"""
To trace mongoengine queries, we patch its connect method::

    # to patch all mongoengine connections, do the following
    # before you import mongoengine connect.

    import mongoengine
    from ddtrace.monkey import patch_all
    patch_all()

    # At that point, mongoengine is instrumented with the default settings
    mongoengine.connect('db', alias='default')

    # To customize all new clients
    from ddtrace import Pin
    Pin(service='my-mongo-cluster').onto(mongoengine.connect)
    mongoengine.connect('db', alias='another')

    # To customize only one client
    client = mongoengine.connect('db', alias='master')
    Pin(service='my-master-mongo-cluster').onto(client)
"""


from ..util import require_modules


required_modules = ['mongoengine']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch

        __all__ = ['patch']
