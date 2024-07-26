from ddtrace.contrib import dbapi_async
from ddtrace.contrib.psycopg.cursor import Psycopg3TracedCursor


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
        return await self.__wrapped__.__aexit__(exc_type, exc_val, exc_tb)


class Psycopg3FetchTracedAsyncCursor(Psycopg3TracedAsyncCursor, dbapi_async.FetchTracedAsyncCursor):
    """Psycopg3FetchTracedAsyncCursor for psycopg"""
