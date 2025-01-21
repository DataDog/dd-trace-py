"""Instrument mongoengine to report MongoDB queries.

``import ddtrace.auto`` will automatically patch your mongoengine connect method to make it work.
::

    from ddtrace import patch
    from ddtrace.trace import Pin
    import mongoengine

    # If not patched yet, you can patch mongoengine specifically
    patch(mongoengine=True)

    # At that point, mongoengine is instrumented with the default settings
    mongoengine.connect('db', alias='default')

    # Use a pin to specify metadata related to this client
    client = mongoengine.connect('db', alias='master')
    Pin.override(client, service="mongo-master")
"""


# Required to allow users to import from  `ddtrace.contrib.mongoengine.patch` directly
import warnings as _w


with _w.catch_warnings():
    _w.simplefilter("ignore", DeprecationWarning)
    from . import patch as _  # noqa: F401, I001


from ddtrace.contrib.internal.mongoengine.patch import get_version  # noqa: F401
from ddtrace.contrib.internal.mongoengine.patch import patch  # noqa: F401
