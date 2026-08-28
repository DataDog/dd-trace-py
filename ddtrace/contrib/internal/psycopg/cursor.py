from typing import Optional
from typing import Union

from ddtrace.contrib import dbapi


class PsycopgTracedCursor(dbapi.TracedCursor):
    """Common cursor tracing for psycopg 2 and 3."""

    def __init__(self, cursor, cfg, *args, **kwargs):
        super(PsycopgTracedCursor, self).__init__(cursor, cfg=cfg, *args, **kwargs)

    def _normalize_dbapi_query(self, query: object) -> Optional[Union[str, bytes]]:
        normalized_query = super(PsycopgTracedCursor, self)._normalize_dbapi_query(query)
        if normalized_query is not None:
            return normalized_query
        renderer = getattr(query, "as_string", None)
        if callable(renderer):
            rendered_query = renderer(self.__wrapped__)
            if isinstance(rendered_query, str):
                return rendered_query
        return None


class Psycopg3TracedCursor(PsycopgTracedCursor):
    """TracedCursor for psycopg 3 instances."""

    def _normalize_dbapi_query(self, query: object) -> Optional[Union[str, bytes]]:
        normalized_query = super(Psycopg3TracedCursor, self)._normalize_dbapi_query(query)
        if normalized_query is not None:
            return normalized_query
        if isinstance(getattr(query, "strings", None), tuple) and isinstance(
            getattr(query, "interpolations", None), tuple
        ):
            renderer = self._self_config.get("_query_renderer")
            if callable(renderer):
                rendered_query = renderer(query, self.__wrapped__)
                if isinstance(rendered_query, str):
                    return rendered_query
        return None


class Psycopg3FetchTracedCursor(Psycopg3TracedCursor, dbapi.FetchTracedCursor):
    """Psycopg3FetchTracedCursor for psycopg"""


class PsycopgFetchTracedCursor(PsycopgTracedCursor, dbapi.FetchTracedCursor):
    """Fetch-tracing cursor for psycopg 2."""
