from datetime import date
from datetime import datetime
from datetime import time
from decimal import Decimal
import sys
from typing import Any
from typing import Optional
from typing import Union
from uuid import UUID

from ddtrace.contrib import dbapi


_REPLAYABLE_LITERAL_TYPES = (str, bytes, int, float, bool, type(None), Decimal, UUID, date, datetime, time)


def _is_replayable_literal(value: Any) -> bool:
    return type(value) in _REPLAYABLE_LITERAL_TYPES


def _is_safe_composable_query(query: Any, sql: Any) -> bool:
    """Check that rendering leaves stateful application values alone."""
    if type(query) in (sql.SQL, sql.Identifier, sql.Placeholder):
        return True
    if type(query) is sql.Literal:
        value = query.wrapped if sql.__name__ == "psycopg2.sql" else query._obj
        return _is_replayable_literal(value)
    if type(query) is sql.Composed:
        return all(_is_safe_composable_query(child, sql) for child in query)
    return False


def _render_composable_query(query: Any, sql: Any, context: object) -> Optional[str]:
    if _is_safe_composable_query(query, sql):
        rendered = query.as_string(context)
        return rendered if isinstance(rendered, str) else None
    return None


def _is_safe_template_query(template: Any, sql: Any) -> bool:
    """Exclude template branches that consume stateful values or run custom renderers."""
    for item in template:
        if isinstance(item, str):
            continue
        if item.conversion:
            return False

        value = item.value
        fmt = item.format_spec
        if isinstance(value, type(template)):
            if fmt != "q" or not _is_safe_template_query(value, sql):
                return False
        elif isinstance(value, sql.Composable):
            if not _is_safe_composable_query(value, sql):
                return False
            if not (
                (type(value) is sql.Identifier and fmt == "i")
                or (type(value) in (sql.SQL, sql.Composed) and fmt == "q")
                or (type(value) is sql.Literal and fmt == "l")
            ):
                return False
        elif fmt == "i":
            if not isinstance(value, str):
                return False
        elif fmt == "l":
            if not _is_replayable_literal(value):
                return False
        elif fmt not in ("", "s", "t", "b"):
            return False
    return True


def _render_template_query(template: Any, sql: Any, context: object) -> Optional[str]:
    """Use psycopg's server-query renderer without dumping bound values."""
    if not _is_safe_template_query(template, sql):
        return None
    tstrings = sys.modules.get("psycopg._tstrings")
    if tstrings is None:
        return None
    tx = sql.Transformer(context)
    processor = tstrings.TemplateProcessor(template, tx=tx, server_params=True)
    processor.process()
    rendered = processor.query.decode(tx.encoding)
    return rendered if isinstance(rendered, str) else None


class Psycopg3TracedCursor(dbapi.TracedCursor):
    """TracedCursor for psycopg instances"""

    def __init__(self, cursor, cfg, *args, **kwargs):
        super().__init__(cursor, cfg=cfg, *args, **kwargs)

    def _query_rendering_context(self) -> object:
        return getattr(self.__wrapped__, "cursor", self.__wrapped__)

    def _render_dbapi_query(self, query: object) -> Optional[Union[str, bytes]]:
        rendered_query = super()._render_dbapi_query(query)
        if rendered_query is not None:
            return rendered_query
        sql = sys.modules.get(query.__class__.__module__)
        if sql is not None and sql.__name__ in ("psycopg.sql", "psycopg2.sql"):
            return _render_composable_query(query, sql, self._query_rendering_context())
        # The driver loads these modules even when only Django instrumentation is enabled.
        # Do not import optional psycopg3/Python 3.14 dependencies for psycopg2 users.
        template_type = getattr(sys.modules.get("string.templatelib"), "Template", ())
        sql = sys.modules.get("psycopg.sql")
        if sql is not None and isinstance(query, template_type):
            return _render_template_query(query, sql, self._query_rendering_context())
        return None

    def _trace_method(self, method, name, resource, extra_tags, dbm_propagator, *args, **kwargs):
        # treat Composable resource objects as strings
        # Django selects this cursor for event rendering, but its tracing behavior must stay generic.
        if self._self_config.integration_name != "django-database" and (
            resource.__class__.__name__ == "SQL" or resource.__class__.__name__ == "Composed"
        ):
            resource = resource.as_string(self.__wrapped__)
        return super()._trace_method(method, name, resource, extra_tags, dbm_propagator, *args, **kwargs)


class Psycopg3FetchTracedCursor(Psycopg3TracedCursor, dbapi.FetchTracedCursor):
    """Psycopg3FetchTracedCursor for psycopg"""


class Psycopg2TracedCursor(Psycopg3TracedCursor):
    """TracedCursor for psycopg2"""


class Psycopg2FetchTracedCursor(Psycopg3FetchTracedCursor):
    """FetchTracedCursor for psycopg2"""
