"""
To trace sqlalchemy queries, add instrumentation to the engine class or
instance you are using::

    from ddtrace import tracer
    from ddtrace.contrib.sqlalchemy import trace_engine
    from sqlalchemy import create_engine

    engine = create_engine('sqlite:///:memory:')
    trace_engine(engine, tracer, "my-database")

    engine.connect().execute("select count(*) from users")

If you are using sqlalchemy in a gevent-ed environment, make sure to monkey patch
the `thread` module prior to importing the global tracer::

    from gevent import monkey; monkey.patch_thread() # or patch_all() if you prefer
    from ddtrace import tracer

    # Add instrumentation to your engine as above
    ...
"""


from ..util import require_modules

required_modules = ['sqlalchemy', 'sqlalchemy.event']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .engine import trace_engine
        __all__ = ['trace_engine']
