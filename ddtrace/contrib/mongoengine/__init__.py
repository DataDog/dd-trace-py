"""
To trace mongoengine queries, we patch its connect method::

    # to patch all mongoengine connections, do the following
    # before you import mongoengine yourself.

    from ddtrace import tracer
    from ddtrace.contrib.mongoengine import trace_mongoengine
    trace_mongoengine(tracer, service="my-mongo-db", patch=True)


    # to patch a single mongoengine connection, do this:
    connect = trace_mongoengine(tracer, service="my-mongo-db", patch=False)
    connect()

    # now use mongoengine ....
    User.objects(name="Mongo")
"""


from ..util import require_modules


required_modules = ['mongoengine']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .trace import trace_mongoengine

        __all__ = ['trace_mongoengine']
