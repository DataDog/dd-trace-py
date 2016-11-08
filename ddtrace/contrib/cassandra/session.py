"""
Trace queries along a session to a cassandra cluster
"""

# stdlib
import logging

# 3p
import cassandra.cluster
import wrapt

# project
from ddtrace import Pin
from ddtrace.compat import stringify
from ...util import deep_getattr
from ...ext import net, cassandra as cassx
from ...ext import AppTypes


log = logging.getLogger(__name__)


RESOURCE_MAX_LENGTH = 5000
SERVICE = "cassandra"


def patch():
    """ patch will add tracing to the cassandra library. """
    patch_cluster(cassandra.cluster.Cluster)

def patch_cluster(cluster, pin=None):
    pin = pin or Pin(service=SERVICE, app=SERVICE)
    setattr(cluster, 'connect', wrapt.FunctionWrapper(cluster.connect, _connect))
    pin.onto(cluster)
    return cluster

def _connect(func, instance, args, kwargs):
    session = func(*args, **kwargs)
    if not isinstance(session.execute, wrapt.FunctionWrapper):
        setattr(session, 'execute', wrapt.FunctionWrapper(session.execute, _execute))
    return session

def _execute(func, instance, args, kwargs):
    cluster = getattr(instance, 'cluster', None)
    pin = Pin.get_from(cluster)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    service = pin.service
    tracer = pin.tracer

    query = kwargs.get("kwargs") or args[0]

    with tracer.trace("cassandra.query", service=service, span_type=cassx.TYPE) as span:
        span.resource = _sanitize_query(query)
        span.set_tags(_extract_session_metas(instance))     # FIXME[matt] do once?
        span.set_tags(_extract_cluster_metas(cluster))
        result = None
        try:
            result = func(*args, **kwargs)
            return result
        finally:
            if result:
                span.set_tags(_extract_result_metas(result))


def get_traced_cassandra(tracer, service=SERVICE, meta=None):
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
        # FIXME the keyspace can be overridden explicitly in the query itself
        # e.g. "select * from trace.hash_to_resource"
        metas[cassx.KEYSPACE] = session.keyspace.lower()

    return metas

def _extract_cluster_metas(cluster):
    metas = {}
    if deep_getattr(cluster, "metadata.cluster_name"):
        metas[cassx.CLUSTER] = cluster.metadata.cluster_name
    if getattr(cluster, "port", None):
        metas[net.TARGET_PORT] = cluster.port
    if getattr(cluster, "compression", None):
        metas[cassx.COMPRESSION] = cluster.compression
    if getattr(cluster, "cql_version", None):
        metas[cassx.CQL_VERSION] = cluster.cql_version

    return metas

def _extract_result_metas(result):
    metas = {}
    if not result:
        return metas
    future = getattr(result, "response_future", None)
    if future:
        host = getattr(future, "coordinator_host", None)
        if host:
            metas[net.TARGET_HOST] = host
        query = getattr(future, "query", None)
        if getattr(query, "consistency_level", None):
            metas[cassx.CONSISTENCY_LEVEL] = query.consistency_level
        if getattr(query, "keyspace", None):
            # Overrides session.keyspace if the query has been prepared against a particular
            # keyspace
            metas[cassx.KEYSPACE] = query.keyspace.lower()

    if hasattr(result, "has_more_pages"):
        metas[cassx.PAGINATED] = bool(result.has_more_pages)

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
