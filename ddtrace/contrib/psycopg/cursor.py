from ddtrace.contrib import dbapi
from ddtrace.contrib import dbapi_async


class Psycopg3TracedCursor(dbapi.TracedCursor):
    """TracedCursor for psycopg instances"""

    def __init__(self, cursor, pin, cfg, *args, **kwargs):
        super(Psycopg3TracedCursor, self).__init__(cursor, pin, cfg)

    def _trace_method(self, method, name, resource, extra_tags, dbm_propagator, *args, **kwargs):
        # treat Composable resource objects as strings
        if resource.__class__.__name__ == "SQL" or resource.__class__.__name__ == "Composed":
            resource = resource.as_string(self.__wrapped__)
        return super(Psycopg3TracedCursor, self)._trace_method(
            method, name, resource, extra_tags, dbm_propagator, *args, **kwargs
        )


class Psycopg3FetchTracedCursor(Psycopg3TracedCursor, dbapi.FetchTracedCursor):
    """Psycopg3FetchTracedCursor for psycopg"""


class Psycopg3TracedAsyncCursor(Psycopg3TracedCursor, dbapi_async.TracedAsyncCursor):
    def __init__(self, cursor, pin, cfg, *args, **kwargs):
        super(Psycopg3TracedAsyncCursor, self).__init__(cursor, pin, cfg)

    async def __aenter__(self):
        # previous versions of the dbapi didn't support context managers. let's
        # reference the func that would be called to ensure that errors
        # messages will be the same.
        await self.__wrapped__.__aenter__()

        # and finally, yield the traced cursor.
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        # previous versions of the dbapi didn't support context managers. let's
        # reference the func that would be called to ensure that errors
        # messages will be the same.
        await self.__wrapped__.__aexit__(exc_type, exc_val, exc_tb)

        # and finally, yield the traced cursor.
        return self


class Psycopg3FetchTracedAsyncCursor(Psycopg3TracedAsyncCursor, dbapi_async.FetchTracedAsyncCursor):
    """Psycopg3FetchTracedAsyncCursor for psycopg"""
