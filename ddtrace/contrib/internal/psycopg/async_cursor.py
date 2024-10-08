from ddtrace.contrib import dbapi_async
from ddtrace.contrib.internal.psycopg.cursor import Psycopg3TracedCursor


class Psycopg3TracedAsyncCursor(Psycopg3TracedCursor, dbapi_async.TracedAsyncCursor):
    def __init__(self, cursor, pin, cfg, *args, **kwargs):
        super(Psycopg3TracedAsyncCursor, self).__init__(cursor, pin, cfg)


class Psycopg3FetchTracedAsyncCursor(Psycopg3TracedAsyncCursor, dbapi_async.FetchTracedAsyncCursor):
    """Psycopg3FetchTracedAsyncCursor for psycopg"""
