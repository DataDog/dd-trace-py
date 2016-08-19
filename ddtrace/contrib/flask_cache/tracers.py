"""
Datadog trace code for flask_cache
"""

# stdlib
import logging

# project
from . import metadata as flaskx
from .utils import _set_span_metas
from ...ext import AppTypes

# 3rd party
from flask.ext.cache import Cache


log = logging.Logger(__name__)

DEFAULT_SERVICE = "flask-cache"


def get_traced_cache(ddtracer, service=DEFAULT_SERVICE, meta=None):
    """
    Return a traced Cache object that behaves exactly as the ``flask.ext.cache.Cache class``
    """

    # set the Tracer info
    ddtracer.set_service_info(
        app="flask",
        app_type=AppTypes.cache,
        service=service,
    )

    class TracedCache(Cache):
        """
        Traced cache backend that monitors any operations done by flask_cash. Observed actions are:
            * get, set, add, delete, clear
            * all many_ operations
        """
        _datadog_tracer = ddtracer
        _datadog_service = service
        _datadog_meta = meta

        def get(self, *args, **kwargs):
            """
            Track ``get`` operation
            """
            with self._datadog_tracer.trace("flask_cache.command") as span:
                if span.sampled:
                    # add default attributes and metas
                    _set_span_metas(self, span, resource="GET")
                    # add span metadata for this tracing
                    span.set_tag(flaskx.COMMAND_KEY, args[0])

                return super(TracedCache, self).get(*args, **kwargs)

        def set(self, *args, **kwargs):
            """
            Track ``set`` operation
            """
            with self._datadog_tracer.trace("flask_cache.command") as span:
                if span.sampled:
                    # add default attributes and metas
                    _set_span_metas(self, span, resource="SET")
                    # add span metadata for this tracing
                    span.set_tag(flaskx.COMMAND_KEY, args[0])

                return super(TracedCache, self).set(*args, **kwargs)

        def add(self, *args, **kwargs):
            """
            Track ``add`` operation
            """
            with self._datadog_tracer.trace("flask_cache.command") as span:
                if span.sampled:
                    # add default attributes and metas
                    _set_span_metas(self, span, resource="ADD")
                    # add span metadata for this tracing
                    span.set_tag(flaskx.COMMAND_KEY, args[0])

                return super(TracedCache, self).add(*args, **kwargs)

        def delete(self, *args, **kwargs):
            """
            Track ``delete`` operation
            """
            with self._datadog_tracer.trace("flask_cache.command") as span:
                if span.sampled:
                    # add default attributes and metas
                    _set_span_metas(self, span, resource="DELETE")
                    # add span metadata for this tracing
                    span.set_tag(flaskx.COMMAND_KEY, args[0])

                return super(TracedCache, self).delete(*args, **kwargs)

        def delete_many(self, *args, **kwargs):
            """
            Track ``delete_many`` operation
            """
            with self._datadog_tracer.trace("flask_cache.command") as span:
                if span.sampled:
                    # add default attributes and metas
                    _set_span_metas(self, span, resource="DELETE_MANY")
                    # add span metadata for this tracing
                    span.set_tag(flaskx.COMMAND_KEY, list(args))

                return super(TracedCache, self).delete_many(*args, **kwargs)

        def clear(self, *args, **kwargs):
            """
            Track ``clear`` operation
            """
            with self._datadog_tracer.trace("flask_cache.command") as span:
                if span.sampled:
                    # add default attributes and metas
                    _set_span_metas(self, span, resource="CLEAR")

                return super(TracedCache, self).clear(*args, **kwargs)

        def get_many(self, *args, **kwargs):
            """
            Track ``get_many`` operation
            """
            with self._datadog_tracer.trace("flask_cache.command") as span:
                if span.sampled:
                    # add default attributes and metas
                    _set_span_metas(self, span, resource="GET_MANY")
                    # add span metadata for this tracing
                    span.set_tag(flaskx.COMMAND_KEY, list(args))

                return super(TracedCache, self).get_many(*args, **kwargs)

        def set_many(self, *args, **kwargs):
            """
            Track ``set_many`` operation
            """
            with self._datadog_tracer.trace("flask_cache.command") as span:
                if span.sampled:
                    # add default attributes and metas
                    _set_span_metas(self, span, resource="SET_MANY")
                    # add span metadata for this tracing
                    span.set_tag(flaskx.COMMAND_KEY, list(args[0].keys()))

                return super(TracedCache, self).set_many(*args, **kwargs)

    return TracedCache
