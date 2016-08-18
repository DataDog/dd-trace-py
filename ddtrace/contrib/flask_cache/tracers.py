"""
Datadog trace code for flask_cache
"""

# stdlib
import logging

# project
from . import metadata as flaskx
from ...ext import AppTypes, net

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
            * inc and dec atomic operations
            * all many_ operations
            * cached and memoize decorators
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

    return TracedCache


def _set_span_metas(traced_cache, span, resource=None):
    """
    Add default attributes to the given ``span``
    """
    # set span attributes
    span.resource = resource
    span.service = traced_cache._datadog_service
    span.span_type = flaskx.TYPE

    # set span metadata
    span.set_tag(flaskx.CACHE_BACKEND, traced_cache.config["CACHE_TYPE"])
    span.set_tags(traced_cache._datadog_meta)
    # add connection meta if there is one
    if getattr(traced_cache.cache, '_client', None):
        span.set_tags(_extract_conn_metas(traced_cache.cache._client))


def _extract_conn_metas(client):
    """
    For the given client, extracts connection tags
    """
    metas = {}

    if getattr(client, "servers", None):
        # Memcached backend supports an address pool
        if isinstance(client.servers, list) and len(client.servers) > 0:
            # add the pool list that are in the format [('<host>', '<port>')]
            pool = [conn.address[0] for conn in client.servers]
            metas[flaskx.CONTACT_POINTS] = pool

            # use the first contact point as a host because
            # the code doesn't expose more information
            contact_point = client.servers[0].address
            metas[net.TARGET_HOST] = contact_point[0]
            metas[net.TARGET_PORT] = contact_point[1]

    if getattr(client, "connection_pool", None):
        # Redis main connection
        conn_kwargs = client.connection_pool.connection_kwargs
        metas[net.TARGET_HOST] = conn_kwargs['host']
        metas[net.TARGET_PORT] = conn_kwargs['port']

    return metas
