"""
To trace cassandra calls, create a traced cassandra client::

    from ddtrace import tracer
    from ddtrace.contrib.cassandra import get_traced_cassandra

    Cluster = get_traced_cassandra(tracer, service="my_cass_service")

    cluster = Cluster(contact_points=["127.0.0.1"], port=9042)
    session = cluster.connect("my_keyspace")
    session.execute("select id from my_table limit 10;")
"""

from ..util import require_modules

required_modules = ['cassandra.cluster']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .session import get_traced_cassandra, patch
        __all__ = [
            'get_traced_cassandra',
            'patch',
        ]
