"""
Datadog trace code for flask_cache
"""

import logging
import typing
from typing import Dict

from ddtrace import config
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_cache_operation
from ddtrace.internal.schema import schematize_service_name

from .utils import _extract_client
from .utils import _extract_conn_tags
from .utils import _resource_from_cache_prefix


if typing.TYPE_CHECKING:  # pragma: no cover
    from ddtrace.trace import Span  # noqa:F401


log = logging.Logger(__name__)

DEFAULT_SERVICE = config.service or schematize_service_name("flask-cache")

# standard tags
COMMAND_KEY = "flask_cache.key"
CACHE_BACKEND = "flask_cache.backend"
CONTACT_POINTS = "flask_cache.contact_points"


def get_version():
    # type: () -> str
    try:
        import flask_caching

        return getattr(flask_caching, "__version__", "")
    except ImportError:
        return ""


def _supported_versions() -> Dict[str, str]:
    return {"flask_cache": ">=0.13"}


def get_traced_cache(ddtracer, service=DEFAULT_SERVICE, meta=None, cache_cls=None):
    """
    Return a traced Cache object that behaves exactly as ``cache_cls``.

    ``cache_cls`` defaults to ``flask.ext.cache.Cache`` if Flask-Cache is installed
    or ``flask_caching.Cache`` if flask-caching is installed.
    """

    if cache_cls is None:
        # for compatibility reason, first check if flask_cache is present
        try:
            from flask.ext.cache import Cache

            cache_cls = Cache
        except ImportError:
            # use flask_caching if flask_cache is not present
            from flask_caching import Cache

            cache_cls = Cache

    class TracedCache(cache_cls):
        """
        Traced cache backend that monitors any operations done by flask_cache. Observed actions are:
        * get, set, add, delete, clear
        * all ``many_`` operations
        """

        _datadog_tracer = ddtracer
        _datadog_service = service
        _datadog_meta = meta

        def __trace(self, cmd):
            # type: (str, bool) -> Span
            """
            Start a tracing with default attributes and tags
            """
            # create a new span
            s = self._datadog_tracer.trace(
                schematize_cache_operation(cmd, cache_provider="flask_cache"),
                span_type=SpanTypes.CACHE,
                service=self._datadog_service,
            )

            s.set_tag_str(COMPONENT, config.flask_cache.integration_name)

            s.set_tag(_SPAN_MEASURED_KEY)
            # set span tags
            s.set_tag_str(CACHE_BACKEND, self.config.get("CACHE_TYPE"))
            s.set_tags(self._datadog_meta)
            # add connection meta if there is one
            client = _extract_client(self.cache)
            if client is not None:
                try:
                    s.set_tags(_extract_conn_tags(client))
                except Exception:
                    log.debug("error parsing connection tags", exc_info=True)

            return s

        def get(self, *args, **kwargs):
            """
            Track ``get`` operation
            """
            with self.__trace("flask_cache.cmd") as span:
                span.resource = _resource_from_cache_prefix("GET", self.config)
                if len(args) > 0:
                    span.set_tag_str(COMMAND_KEY, args[0])
                result = super(TracedCache, self).get(*args, **kwargs)
                span.set_metric(db.ROWCOUNT, 1 if result else 0)
                return result

        def set(self, *args, **kwargs):
            """
            Track ``set`` operation
            """
            with self.__trace("flask_cache.cmd") as span:
                span.resource = _resource_from_cache_prefix("SET", self.config)
                if len(args) > 0:
                    span.set_tag_str(COMMAND_KEY, args[0])
                return super(TracedCache, self).set(*args, **kwargs)

        def add(self, *args, **kwargs):
            """
            Track ``add`` operation
            """
            with self.__trace("flask_cache.cmd") as span:
                span.resource = _resource_from_cache_prefix("ADD", self.config)
                if len(args) > 0:
                    span.set_tag_str(COMMAND_KEY, args[0])
                return super(TracedCache, self).add(*args, **kwargs)

        def delete(self, *args, **kwargs):
            """
            Track ``delete`` operation
            """
            with self.__trace("flask_cache.cmd") as span:
                span.resource = _resource_from_cache_prefix("DELETE", self.config)
                if len(args) > 0:
                    span.set_tag_str(COMMAND_KEY, args[0])
                return super(TracedCache, self).delete(*args, **kwargs)

        def delete_many(self, *args, **kwargs):
            """
            Track ``delete_many`` operation
            """
            with self.__trace("flask_cache.cmd") as span:
                span.resource = _resource_from_cache_prefix("DELETE_MANY", self.config)
                span.set_tag(COMMAND_KEY, list(args))
                return super(TracedCache, self).delete_many(*args, **kwargs)

        def clear(self, *args, **kwargs):
            """
            Track ``clear`` operation
            """
            with self.__trace("flask_cache.cmd") as span:
                span.resource = _resource_from_cache_prefix("CLEAR", self.config)
                return super(TracedCache, self).clear(*args, **kwargs)

        def get_many(self, *args, **kwargs):
            """
            Track ``get_many`` operation
            """
            with self.__trace("flask_cache.cmd") as span:
                span.resource = _resource_from_cache_prefix("GET_MANY", self.config)
                span.set_tag(COMMAND_KEY, list(args))
                result = super(TracedCache, self).get_many(*args, **kwargs)
                # get many returns a list, with either the key value or None if it doesn't exist
                span.set_metric(db.ROWCOUNT, sum(1 for val in result if val))
                return result

        def set_many(self, *args, **kwargs):
            """
            Track ``set_many`` operation
            """
            with self.__trace("flask_cache.cmd") as span:
                span.resource = _resource_from_cache_prefix("SET_MANY", self.config)
                if len(args) > 0:
                    span.set_tag(COMMAND_KEY, list(args[0].keys()))
                return super(TracedCache, self).set_many(*args, **kwargs)

    return TracedCache
