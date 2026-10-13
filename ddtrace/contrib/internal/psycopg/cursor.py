import sys
from typing import Union

from ddtrace.contrib import dbapi
from ddtrace.internal import core


class Psycopg3TracedCursor(dbapi.TracedCursor):
    """TracedCursor for psycopg instances"""

    def __init__(self, cursor, cfg, *args, **kwargs):
        super().__init__(cursor, cfg=cfg, *args, **kwargs)

    def _decode_dbapi_query(self, query: Union[str, bytes]) -> Union[str, bytes]:
        if isinstance(query, bytes) and core.has_listeners(dbapi.DbQueryEvent.event_name):
            connection = self.__wrapped__.connection
            if hasattr(connection, "encoding"):
                encoding = sys.modules["psycopg2.extensions"].encodings[connection.encoding]
            else:
                encoding = connection.info.encoding
            if encoding not in ("utf-8", "utf8", "utf_8"):
                return query.decode(encoding)
        return query

    def _trace_method(self, method, name, resource, extra_tags, dbm_propagator, *args, **kwargs):
        # treat Composable resource objects as strings
        if resource.__class__.__name__ == "SQL" or resource.__class__.__name__ == "Composed":
            resource = resource.as_string(self.__wrapped__)
        return super()._trace_method(method, name, resource, extra_tags, dbm_propagator, *args, **kwargs)


class Psycopg3FetchTracedCursor(Psycopg3TracedCursor, dbapi.FetchTracedCursor):
    """Psycopg3FetchTracedCursor for psycopg"""


class Psycopg2TracedCursor(Psycopg3TracedCursor):
    """TracedCursor for psycopg2"""


class Psycopg2FetchTracedCursor(Psycopg3FetchTracedCursor):
    """FetchTracedCursor for psycopg2"""
