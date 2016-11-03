"""
To trace sqlalchemy queries, add instrumentation to the engine class or
instance you are using::

    from ddtrace import tracer
    from ddtrace.contrib.sqlalchemy import trace_engine
    from sqlalchemy import create_engine

    engine = create_engine('sqlite:///:memory:')
    trace_engine(engine, tracer, "my-database")

    engine.connect().execute("select count(*) from users")
"""


from ..util import require_modules

required_modules = ['sqlalchemy', 'sqlalchemy.event']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .engine import trace_engine
        __all__ = ['trace_engine']
