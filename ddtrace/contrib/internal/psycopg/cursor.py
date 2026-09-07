import sys
from typing import Any
from typing import Optional
from typing import Union

from ddtrace.contrib import dbapi


def _render_composable_query(query: Any, sql: Any, context: object) -> Optional[str]:
    """Render only built-in SQL structure, without invoking parameter adapters."""
    # AIDEV-NOTE: Only built-in structural SQL nodes are safe: Literal and custom
    # composables can invoke stateful dumpers, changing the subsequent execution.
    if type(query) in (sql.SQL, sql.Identifier, sql.Placeholder):
        rendered = query.as_string(context)
        return rendered if isinstance(rendered, str) else None
    if type(query) is sql.Composed:
        parts = []
        for child in query:
            rendered = _render_composable_query(child, sql, context)
            if rendered is None:
                return None
            parts.append(rendered)
        return "".join(parts)
    return None


def _render_template_query(template: Any, sql: Any, context: object) -> Optional[str]:
    """Render SQL structure without adapting template parameters."""
    parameter_count = 0

    def render_template(value: Any) -> Optional[str]:
        nonlocal parameter_count
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
                continue
            if item.conversion:
                return None

            parameter = item.value
            fmt = item.format_spec
            if isinstance(parameter, type(template)):
                rendered = render_template(parameter) if fmt == "q" else None
            elif isinstance(parameter, sql.Composable):
                if (type(parameter) is sql.Identifier and fmt == "i") or (
                    type(parameter) in (sql.SQL, sql.Composed) and fmt == "q"
                ):
                    rendered = _render_composable_query(parameter, sql, context)
                else:
                    return None
            elif fmt == "i" and isinstance(parameter, str):
                rendered = sql.Identifier(parameter).as_string(context)
            elif fmt in ("", "s", "t", "b"):
                parameter_count += 1
                rendered = f"${parameter_count}"
            else:
                # Literal interpolation requires adaptation; leave it to the driver.
                return None
            if rendered is None:
                return None
            parts.append(rendered)
        return "".join(parts)

    return render_template(template)


class PsycopgTracedCursor(dbapi.TracedCursor):
    """Common cursor tracing for psycopg 2 and 3."""

    def __init__(self, cursor, cfg, *args, **kwargs):
        super(PsycopgTracedCursor, self).__init__(cursor, cfg=cfg, *args, **kwargs)

    def _query_rendering_context(self) -> object:
        return getattr(self.__wrapped__, "cursor", self.__wrapped__)

    def _normalize_dbapi_query(self, query: object) -> Optional[Union[str, bytes]]:
        normalized_query = super(PsycopgTracedCursor, self)._normalize_dbapi_query(query)
        if normalized_query is not None:
            return normalized_query
        # Preserve existing standalone SQL/Composed rendering, without broadening it
        # to arbitrary custom renderers or literal adapters.
        if query.__class__.__name__ in ("SQL", "Composed"):
            rendered_query = getattr(query, "as_string")(self._query_rendering_context())
            if isinstance(rendered_query, str):
                return rendered_query
        sql = sys.modules.get(query.__class__.__module__)
        if sql is not None and sql.__name__ in ("psycopg.sql", "psycopg2.sql"):
            return _render_composable_query(query, sql, self._query_rendering_context())
        return None


class Psycopg3TracedCursor(PsycopgTracedCursor):
    """TracedCursor for psycopg 3 instances."""

    def _normalize_dbapi_query(self, query: object) -> Optional[Union[str, bytes]]:
        normalized_query = super(Psycopg3TracedCursor, self)._normalize_dbapi_query(query)
        if normalized_query is not None:
            return normalized_query
        # The driver loads these modules even when only Django instrumentation is enabled.
        # Do not import optional psycopg3/Python 3.14 dependencies for psycopg2 users.
        template_type = getattr(sys.modules.get("string.templatelib"), "Template", ())
        sql = sys.modules.get("psycopg.sql")
        if sql is not None and isinstance(query, template_type):
            return _render_template_query(query, sql, self._query_rendering_context())
        return None


class Psycopg3FetchTracedCursor(Psycopg3TracedCursor, dbapi.FetchTracedCursor):
    """Psycopg3FetchTracedCursor for psycopg"""


class PsycopgFetchTracedCursor(PsycopgTracedCursor, dbapi.FetchTracedCursor):
    """Fetch-tracing cursor for psycopg 2."""
