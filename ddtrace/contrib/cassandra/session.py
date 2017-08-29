"""
Trace queries along a session to a cassandra cluster
"""
import sys
import logging
# 3p
import cassandra.cluster
import wrapt

# project
from ddtrace import Pin
from ddtrace.compat import stringify
from ...util import deep_getattr, deprecated
from ...ext import net, cassandra as cassx

log = logging.getLogger(__name__)

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
        # FIXME[matt] this should probably be private.
        setattr(session, 'execute_async', wrapt.FunctionWrapper(session.execute_async, traced_execute_async))
    return session


def traced_execute_async_callback(results, future):
    span = getattr(future, '_current_span')
    if not span:
        log.debug('Callback was unable to get the current span from the ResponseFuture')
        return
    span.set_tags(_extract_result_metas(cassandra.cluster.ResultSet(future, results)))
    span.finish()

def traced_execute_async_errback(exc, future):
    span = getattr(future, '_current_span')
    if not span:
        log.debug('Errback was unable to get the current span from the ResponseFuture')
        return
    # FIXME how should we handle an exception that hasn't be rethrown yet
    try:
        raise exc
    except:
        span.set_exc_info(*sys.exc_info())
    span.finish()

def safe_clear_callbacks(func, instance, args, kwargs):
    """
    This overrides the original clear_callbacks of the ResponseFuture to make
    sure our callbacks are not removed by application code.
    """
    try:
        callback_lock = getattr(instance, '_callback_lock')
        with callback_lock:
            callbacks = getattr(instance, '_callbacks')
            errbacks = getattr(instance, '_errbacks')
            callbacks = [callbacks[0]] if callbacks else []
            errbacks = [errbacks[0]] if errbacks else []
    except:
        func(*args, **kwargs)

def traced_execute_async(func, instance, args, kwargs):
    cluster = getattr(instance, 'cluster', None)
    pin = Pin.get_from(cluster)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    service = pin.service
    tracer = pin.tracer

    query = kwargs.get("kwargs") or args[0]

    span = tracer.trace("cassandra.query", service=service, span_type=cassx.TYPE)
    _sanitize_query(span, query)
    span.set_tags(_extract_session_metas(instance))     # FIXME[matt] do once?
    span.set_tags(_extract_cluster_metas(cluster))
    try:
        result = func(*args, **kwargs)
        result.add_callbacks(
            traced_execute_async_callback,
            traced_execute_async_errback,
            callback_args=(result,),
            errback_args=(result,)
        )
        setattr(result, '_current_span', span)
        setattr(result, 'clear_callbacks', wrapt.FunctionWrapper(result.clear_callbacks, safe_clear_callbacks))
        return result
    except:
        span.set_exc_info(*sys.exc_info())
        span.finish()
        raise

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
        # get the host
        host = getattr(future, "coordinator_host", None)
        if host:
            metas[net.TARGET_HOST] = host
        elif hasattr(future, '_current_host'):
            address = deep_getattr(future, '_current_host.address')
            if address:
                metas[net.TARGET_HOST] = address

        query = getattr(future, "query", None)
        if getattr(query, "consistency_level", None):
            metas[cassx.CONSISTENCY_LEVEL] = query.consistency_level
        if getattr(query, "keyspace", None):
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
    # TODO (aaditya): fix this hacky type check. we need it to avoid circular imports
    t = type(query).__name__

    resource = None
    if t in ('SimpleStatement', 'PreparedStatement'):
        # reset query if a string is available
        resource = getattr(query, "query_string", query)
    elif t == 'BatchStatement':
        resource = 'BatchStatement'
        q = "; ".join(q[1] for q in query._statements_and_parameters[:2])
        span.set_tag("cassandra.query", q)
        span.set_metric("cassandra.batch_size", len(query._statements_and_parameters))
    elif t == 'BoundStatement':
        ps = getattr(query, 'prepared_statement', None)
        if ps:
            resource = getattr(ps, 'query_string', None)
    elif t == 'str':
        resource = query
    else:
        resource = 'unknown-query-type' # FIXME[matt] what else do to here?

    span.resource = stringify(resource)[:RESOURCE_MAX_LENGTH]


#
# DEPRECATED
#

@deprecated(message='Use patching instead (see the docs).', version='0.6.0')
def get_traced_cassandra(*args, **kwargs):
    return _get_traced_cluster(*args, **kwargs)


def _get_traced_cluster(*args, **kwargs):
    return cassandra.cluster.Cluster
