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
CURRENT_SPAN = "_ddtrace_current_span"
PAGE_NUMBER = "_ddtrace_page_number"

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

def traced_set_final_result(func, instance, args, kwargs):
    result = kwargs.get('result') or args[0]
    span = getattr(instance, CURRENT_SPAN, None)
    if not span:
        log.debug('traced_set_final_result was not able to get the current span from the ResponseFuture')
        return func(*args, **kwargs)
    with span:
        span.set_tags(_extract_result_metas(cassandra.cluster.ResultSet(instance, result)))
    return func(*args, **kwargs)

def traced_set_final_exception(func, instance, args, kwargs):
    exc = kwargs.get('result') or args[0]
    span = getattr(instance, CURRENT_SPAN, None)
    if not span:
        log.debug('traced_set_final_exception was not able to get the current span from the ResponseFuture')
        return func(*args, **kwargs)
    with span:
        # FIXME how should we handle an exception that hasn't be rethrown yet
        try:
            raise exc
        except:
            span.set_exc_info(*sys.exc_info())
    return func(*args, **kwargs)

def traced_start_fetching_next_page(func, instance, args, kwargs):
    has_more_pages = instance._paging_state is not None
    if not has_more_pages:
        return func(*args, **kwargs)
    session = getattr(instance, 'session', None)
    cluster = getattr(session, 'cluster', None)
    pin = Pin.get_from(cluster)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    # In case the current span is not finished we make sure to finish it
    old_span = getattr(instance, CURRENT_SPAN, None)
    if old_span:
        log.debug('previous span was not finished before fetching next page')
        old_span.finish()

    query = getattr(instance, 'query', None)
    page_number = getattr(instance, PAGE_NUMBER, 0) + 1

    span = _start_span_and_set_tags(pin, query, session, cluster, page_number)

    setattr(instance, PAGE_NUMBER, page_number)
    setattr(instance, CURRENT_SPAN, span)
    try:
        return func(*args, **kwargs)
    except:
        span.set_exc_info(*sys.exc_info())
        span.finish()
        raise

def traced_execute_async(func, instance, args, kwargs):
    cluster = getattr(instance, 'cluster', None)
    pin = Pin.get_from(cluster)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    query = kwargs.get("kwargs") or args[0]

    span = _start_span_and_set_tags(pin, query, instance, cluster, page_number=1)

    try:
        result = func(*args, **kwargs)
        setattr(result, CURRENT_SPAN, span)
        setattr(result, PAGE_NUMBER, 1)
        setattr(result, '_set_final_result', wrapt.FunctionWrapper(result._set_final_result, traced_set_final_result))
        setattr(result, '_set_final_exception', wrapt.FunctionWrapper(result._set_final_exception, traced_set_final_exception))
        setattr(result, 'start_fetching_next_page', wrapt.FunctionWrapper(result.start_fetching_next_page, traced_start_fetching_next_page))
        return result
    except:
        span.set_exc_info(*sys.exc_info())
        span.finish()
        raise

def _start_span_and_set_tags(pin, query, session, cluster, page_number):
    service = pin.service
    tracer = pin.tracer
    span = tracer.trace("cassandra.query", service=service, span_type=cassx.TYPE)
    _sanitize_query(span, query)
    span.set_tags(_extract_session_metas(session))     # FIXME[matt] do once?
    span.set_tags(_extract_cluster_metas(cluster))
    span.set_tag(cassx.PAGE_NUMBER, page_number)
    return span

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
    if result is None:
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
