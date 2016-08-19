# project
from . import metadata as flaskx
from ...ext import net


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
