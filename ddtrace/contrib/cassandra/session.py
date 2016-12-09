"""
Trace queries along a session to a cassandra cluster
"""
# 3p
import cassandra.cluster
import wrapt

# project
from ddtrace import Pin
from ddtrace.compat import stringify
from ...util import deep_getattr, deprecated
from ...ext import net, cassandra as cassx
from ...ext import AppTypes


RESOURCE_MAX_LENGTH = 5000
SERVICE = "cassandra"

# Original connect connect function
_connect = cassandra.cluster.Cluster.connect

def patch():
    """ patch will add tracing to the cassandra library. """
    setattr(cassandra.cluster.Cluster, 'connect',
            wrapt.FunctionWrapper(_connect, traced_connect))
    Pin(service=SERVICE, app=SERVICE, app_type="db").onto(cassandra.cluster.Cluster)

def unpatch():
    cassandra.cluster.Cluster.connect = _connect

def traced_connect(func, instance, args, kwargs):
    session = func(*args, **kwargs)
    if not isinstance(session.execute, wrapt.FunctionWrapper):
        setattr(session, 'execute', wrapt.FunctionWrapper(session.execute, traced_execute))
    return session

def traced_execute(func, instance, args, kwargs):
    cluster = getattr(instance, 'cluster', None)
    pin = Pin.get_from(cluster)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    service = pin.service
    tracer = pin.tracer

    query = kwargs.get("kwargs") or args[0]

    with tracer.trace("cassandra.query", service=service, span_type=cassx.TYPE) as span:
        span = _sanitize_query(span, query)
        span.set_tags(_extract_session_metas(instance))     # FIXME[matt] do once?
        span.set_tags(_extract_cluster_metas(cluster))
        result = None
        try:
            result = func(*args, **kwargs)
            return result
        finally:
            if result:
                span.set_tags(_extract_result_metas(result))


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
            # keyspace.
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

def _sanitize_query(span, query):
    """ Sanitize the query to something ready for the agent receiver
    - Cast to unicode
    - truncate if needed
    """
    # TODO (aaditya): fix this hacky type check. we need it to avoid circular imports
    t = type(query).__name__

    resource = None
    if t in ('SimpleStatement', 'PreparedStatement'):
        # reset query if a string is available
        resource  = getattr(query, "query_string", query)
    elif t == 'BatchStatement':
        resource = 'BatchStatement'
        q = "; ".join(q[1] for q in query._statements_and_parameters[:2])
        span.set_tag("cassandra.query", q)
        span.set_metric("cassandra.batch_size", len(query._statements_and_parameters))
    elif t == 'BoundStatement':
        ps = getattr(query, 'prepared_statement', None)
        if ps:
            resource = getattr(ps, 'query_string', None)
    else:
        resource = 'unknown-query-type' # FIXME[matt] what else do to here?

    span.resource = stringify(resource)[:RESOURCE_MAX_LENGTH]
    return span


#
# DEPRECATED
#

@deprecated(message='Use patching instead (see the docs).', version='0.6.0')
def get_traced_cassandra(tracer, service=SERVICE, meta=None):
    return _get_traced_cluster(cassandra.cluster, tracer, service, meta)


def _get_traced_cluster(cassandra, tracer, service="cassandra", meta=None):
    """ Trace synchronous cassandra commands by patching the Session class """
    return cassandra.Cluster



