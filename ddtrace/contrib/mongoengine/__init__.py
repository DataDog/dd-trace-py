"""Instrument mongoengine to report MongoDB queries.

Patch your mongoengine connect method to make it work.

    # to patch all mongoengine connections, do the following
    # before you import mongoengine connect.

    from ddtrace import patch, Pin
    import mongoengine
    patch(mongoengine=True)

    # At that point, mongoengine is instrumented with the default settings
    mongoengine.connect('db', alias='default')

    # To customize one client instrumentation
    client = mongoengine.connect('db', alias='master')
    Pin(service='my-master-mongo-cluster').onto(client)
"""

from ..util import require_modules


required_modules = ['mongoengine']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch

        __all__ = ['patch']
