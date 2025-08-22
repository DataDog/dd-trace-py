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
