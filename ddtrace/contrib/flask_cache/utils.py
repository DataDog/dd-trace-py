# project
from ...ext import net
from ..redis.util import _extract_conn_tags as extract_redis_tags


def _resource_from_cache_prefix(resource, cache):
    """
    Combine the resource name with the cache prefix (if any)
    """
    if getattr(cache, "key_prefix", None):
        return "{} {}".format(resource, cache.key_prefix)
    else:
        return resource


def _extract_conn_tags(client):
    """
    For the given client extracts connection tags
    """
    tags = {}

    if getattr(client, "servers", None):
        # Memcached backend supports an address pool
        if isinstance(client.servers, list) and len(client.servers) > 0:
            # use the first address of the pool as a host because
            # the code doesn't expose more information
            contact_point = client.servers[0].address
            tags[net.TARGET_HOST] = contact_point[0]
            tags[net.TARGET_PORT] = contact_point[1]

    if getattr(client, "connection_pool", None):
        # Redis main connection
        redis_tags = extract_redis_tags(client.connection_pool.connection_kwargs)
        tags.update(**redis_tags)

    return tags
