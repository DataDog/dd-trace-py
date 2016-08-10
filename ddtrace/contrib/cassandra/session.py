"""
Trace queries along a session to a cassandra cluster
"""

# stdlib
import logging


# project
from ...compat import stringify
from ...util import deep_getattr
from ...ext import net as netx, cassandra as cassx
from ...ext import AppTypes

# 3p
import cassandra.cluster


log = logging.getLogger(__name__)

RESOURCE_MAX_LENGTH = 5000
DEFAULT_SERVICE = "cassandra"


def get_traced_cassandra(tracer, service=DEFAULT_SERVICE, meta=None):
    return _get_traced_cluster(cassandra.cluster, tracer, service, meta)


def _get_traced_cluster(cassandra, tracer, service="cassandra", meta=None):
    """ Trace synchronous cassandra commands by patching the Session class """

    tracer.set_service_info(
        service=service,
        app="cassandra",
        app_type=AppTypes.db,
    )

    class TracedSession(cassandra.Session):
        _dd_tracer = tracer
        _dd_service = service
        _dd_tags = meta

        def __init__(self, *args, **kwargs):
            super(TracedSession, self).__init__(*args, **kwargs)

        def execute(self, query, *args, **options):
            if not self._dd_tracer:
                return super(TracedSession, self).execute(query, *args, **options)

            with self._dd_tracer.trace("cassandra.query", service=self._dd_service) as span:
                query_string = _sanitize_query(query)
                span.resource = query_string
                span.span_type = cassx.TYPE

                span.set_tags(_extract_session_metas(self))
                cluster = getattr(self, "cluster", None)
                span.set_tags(_extract_cluster_metas(cluster))

                result = None
                try:
                    result = super(TracedSession, self).execute(query, *args, **options)
                    return result
                finally:
                    span.set_tags(_extract_result_metas(result))

    class TracedCluster(cassandra.Cluster):

        def connect(self, *args, **kwargs):
            orig = cassandra.Session
            cassandra.Session = TracedSession
            traced_session = super(TracedCluster, self).connect(*args, **kwargs)

            # unpatch the Session class so we don't wrap already traced sessions
            cassandra.Session = orig

            return traced_session

    return TracedCluster

def _extract_session_metas(session):
    metas = {}

    if getattr(session, "keyspace", None):
        # NOTE the keyspace can be overridden explicitly in the query itself
        # e.g. "Select * from trace.hash_to_resource"
        # currently we don't account for this, which is probably fine
        # since the keyspace info is contained in the query even if the metadata disagrees
        metas[cassx.KEYSPACE] = session.keyspace.lower()

    return metas

def _extract_cluster_metas(cluster):
    metas = {}
    if deep_getattr(cluster, "metadata.cluster_name"):
        metas[cassx.CLUSTER] = cluster.metadata.cluster_name

    if getattr(cluster, "port", None):
        metas[netx.TARGET_PORT] = cluster.port

    if getattr(cluster, "contact_points", None):
        metas[cassx.CONTACT_POINTS] = cluster.contact_points
        # Use the first contact point as a persistent host
        if isinstance(cluster.contact_points, list) and len(cluster.contact_points) > 0:
            metas[netx.TARGET_HOST] = cluster.contact_points[0]

    if getattr(cluster, "compression", None):
        metas[cassx.COMPRESSION] = cluster.compression
    if getattr(cluster, "cql_version", None):
        metas[cassx.CQL_VERSION] = cluster.cql_version

    return metas

def _extract_result_metas(result):
    metas = {}
    if not result:
        return metas

    if deep_getattr(result, "response_future.query"):
        query = result.response_future.query

        if getattr(query, "consistency_level", None):
            metas[cassx.CONSISTENCY_LEVEL] = query.consistency_level
        if getattr(query, "keyspace", None):
            # Overrides session.keyspace if the query has been prepared against a particular
            # keyspace
            metas[cassx.KEYSPACE] = query.keyspace.lower()

    if hasattr(result, "has_more_pages"):
        if result.has_more_pages:
            metas[cassx.PAGINATED] = True
        else:
            metas[cassx.PAGINATED] = False

    # NOTE(aaditya): this number only reflects the first page of results
    # which could be misleading. But a true count would require iterating through
    # all pages which is expensive
    if hasattr(result, "current_rows"):
        result_rows = result.current_rows or []
        metas[cassx.ROW_COUNT] = len(result_rows)

    return metas

def _sanitize_query(query):
    """ Sanitize the query to something ready for the agent receiver
    - Cast to unicode
    - truncate if needed
    """
    # TODO (aaditya): fix this hacky type check. we need it to avoid circular imports
    if type(query).__name__ in ('SimpleStatement', 'PreparedStatement'):
        # reset query if a string is available
        query = getattr(query, "query_string", query)

    return stringify(query)[:RESOURCE_MAX_LENGTH]
