import sys
from typing import Optional
from typing import Union

from ddtrace.contrib import dbapi


class Psycopg3TracedCursor(dbapi.TracedCursor):
    """TracedCursor for psycopg instances"""

    def __init__(self, cursor, cfg, *args, **kwargs):
        super().__init__(cursor, cfg=cfg, *args, **kwargs)

    def _render_dbapi_query(self, query: object) -> Optional[Union[str, bytes]]:
        rendered_query = super()._render_dbapi_query(query)
        if rendered_query is not None:
            return rendered_query
        for module_name in ("psycopg.sql", "psycopg2.sql"):
            sql = sys.modules.get(module_name)
            if sql is not None and isinstance(query, sql.Composable):
                context = getattr(self.__wrapped__, "cursor", self.__wrapped__)
                rendered_query = query.as_string(context)
                return rendered_query if isinstance(rendered_query, str) else None
        template_type = getattr(sys.modules.get("string.templatelib"), "Template", ())
        if isinstance(query, template_type):
            psycopg = sys.modules.get("psycopg")
            sql = sys.modules.get("psycopg.sql")
            if psycopg is None or sql is None:
                return None
            context = getattr(self.__wrapped__, "cursor", self.__wrapped__)
            if isinstance(context, (psycopg.ClientCursor, psycopg.AsyncClientCursor)):
                rendered_query = sql.as_string(query, context)
                return rendered_query if isinstance(rendered_query, str) else None
            tstrings = sys.modules.get("psycopg._tstrings")
            if tstrings is None:
                return None
            # AIDEV-NOTE: Match psycopg's server query path without dumping bound values.
            tx = sql.Transformer(context)
            processor = tstrings.TemplateProcessor(query, tx=tx, server_params=True)
            processor.process()
            rendered_query = processor.query.decode(tx.encoding)
            return rendered_query if isinstance(rendered_query, str) else None
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
