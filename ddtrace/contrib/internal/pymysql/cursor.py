from ddtrace.contrib.dbapi import FetchTracedCursor
from ddtrace.contrib.dbapi import TracedCursor


class PyMySQLTracedCursor(TracedCursor):
    def _dbapi_query_encoding(self) -> str:
        encoding: str = self.__wrapped__.connection.encoding
        return encoding


class PyMySQLFetchTracedCursor(PyMySQLTracedCursor, FetchTracedCursor):
    pass
