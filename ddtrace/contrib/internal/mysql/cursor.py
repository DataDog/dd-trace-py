from ddtrace.contrib.dbapi import FetchTracedCursor
from ddtrace.contrib.dbapi import TracedCursor
from ddtrace.contrib.dbapi_async import FetchTracedAsyncCursor
from ddtrace.contrib.dbapi_async import TracedAsyncCursor


class MySQLTracedCursor(TracedCursor):
    def _dbapi_query_encoding(self) -> str:
        cursor = self.__wrapped__
        # Cursors only reference their connection privately, as _cnx in older C extension cursors.
        connection = cursor._cnx if hasattr(cursor, "_cnx") else cursor._connection
        encoding: str = connection.python_charset
        return encoding


class MySQLFetchTracedCursor(MySQLTracedCursor, FetchTracedCursor):
    pass


class MySQLTracedAsyncCursor(MySQLTracedCursor, TracedAsyncCursor):
    pass


class MySQLFetchTracedAsyncCursor(MySQLTracedAsyncCursor, FetchTracedAsyncCursor):
    pass
