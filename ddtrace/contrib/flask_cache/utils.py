# project
from ...ext import net
from ..redis.util import _extract_conn_tags as extract_redis_tags
from ..pylibmc.addrs import parse_addresses


def _extract_conn_tags(client):
    """
    For the given client extracts connection tags
    """
    tags = {}

    if hasattr(client, "servers"):
        # Memcached backend supports an address pool
        if isinstance(client.servers, list) and len(client.servers) > 0:
            # use the first address of the pool as a host because
            # the code doesn't expose more information
            contact_point = client.servers[0].address
            tags[net.TARGET_HOST] = contact_point[0]
            tags[net.TARGET_PORT] = contact_point[1]
    elif hasattr(client, "connection_pool"):
        # Redis main connection
        redis_tags = extract_redis_tags(client.connection_pool.connection_kwargs)
        tags.update(**redis_tags)
    elif hasattr(client, "addresses"):
        # pylibmc
        # FIXME[matt] should we memoize this?
        addrs = parse_addresses(client.addresses)
        if addrs:
            _, host, port, _ = addrs[0]
            tags[net.TARGET_PORT] = port
            tags[net.TARGET_HOST] = host
    return tags
