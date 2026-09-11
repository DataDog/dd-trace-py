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


class Psycopg3TracedCursor(dbapi.TracedCursor):
    """TracedCursor for psycopg instances"""

    def __init__(self, cursor, cfg, *args, **kwargs):
        super(Psycopg3TracedCursor, self).__init__(cursor, cfg=cfg, *args, **kwargs)

    def _query_rendering_context(self) -> object:
        return getattr(self.__wrapped__, "cursor", self.__wrapped__)

    def _render_dbapi_query(self, query: object) -> Optional[Union[str, bytes]]:
        rendered_query = super(Psycopg3TracedCursor, self)._render_dbapi_query(query)
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
        return super(Psycopg3TracedCursor, self)._trace_method(
            method, name, resource, extra_tags, dbm_propagator, *args, **kwargs
        )


class Psycopg3FetchTracedCursor(Psycopg3TracedCursor, dbapi.FetchTracedCursor):
    """Psycopg3FetchTracedCursor for psycopg"""


class Psycopg2TracedCursor(Psycopg3TracedCursor):
    """TracedCursor for psycopg2"""


class Psycopg2FetchTracedCursor(Psycopg3FetchTracedCursor):
    """FetchTracedCursor for psycopg2"""
