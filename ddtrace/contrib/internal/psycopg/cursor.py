import sys

from ddtrace.contrib import dbapi


def psycopg2_python_encoding(connection) -> str:
    # psycopg2 reports PostgreSQL encoding names, like WIN1252. It is looked up rather than imported because
    # this module also serves psycopg 3, and it is always loaded when a psycopg2 connection exists.
    encoding: str = sys.modules["psycopg2.extensions"].encodings[connection.encoding]
    return encoding


class Psycopg3TracedCursor(dbapi.TracedCursor):
    """TracedCursor for psycopg instances"""

    def __init__(self, cursor, cfg, *args, **kwargs):
        super().__init__(cursor, cfg=cfg, *args, **kwargs)

    def _dbapi_query_encoding(self) -> str:
        encoding: str = self.__wrapped__.connection.info.encoding
        return encoding

    def _trace_method(self, method, name, resource, extra_tags, dbm_propagator, *args, **kwargs):
        # treat Composable resource objects as strings
        if resource.__class__.__name__ == "SQL" or resource.__class__.__name__ == "Composed":
            resource = resource.as_string(self.__wrapped__)
        return super()._trace_method(method, name, resource, extra_tags, dbm_propagator, *args, **kwargs)


class Psycopg3FetchTracedCursor(Psycopg3TracedCursor, dbapi.FetchTracedCursor):
    """Psycopg3FetchTracedCursor for psycopg"""


class Psycopg2TracedCursor(Psycopg3TracedCursor):
    """TracedCursor for psycopg2"""

    def _dbapi_query_encoding(self) -> str:
        return psycopg2_python_encoding(self.__wrapped__.connection)


class Psycopg2FetchTracedCursor(Psycopg2TracedCursor, Psycopg3FetchTracedCursor):
    """FetchTracedCursor for psycopg2"""
