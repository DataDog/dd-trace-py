
# 3p
import mongoengine
import wrapt

# project
from ddtrace.ext import mongo as mongox
from ddtrace.contrib.pymongo import trace_mongo_client


def trace_mongoengine(tracer, service=mongox.TYPE, patch=False):
    connect = mongoengine.connect
    wrapped = WrappedConnect(connect, tracer, service)
    if patch:
        mongoengine.connect = wrapped
    return wrapped


class WrappedConnect(wrapt.ObjectProxy):
    """ WrappedConnect wraps mongoengines 'connect' function to ensure
        that all returned connections are wrapped for tracing.
    """

    _service = None
    _tracer = None

    def __init__(self, connect, tracer, service):
        super(WrappedConnect, self).__init__(connect)
        self._service = service
        self._tracer = tracer

    def __call__(self, *args, **kwargs):
        client = self.__wrapped__(*args, **kwargs)
        if _is_traced(client):
            return client
        # mongoengine uses pymongo internally, so we can just piggyback on the
        # existing pymongo integration and make sure that the connections it
        # uses internally are traced.
        return trace_mongo_client(
            client,
            tracer=self._tracer,
            service=self._service)


def _is_traced(client):
    return isinstance(client, wrapt.ObjectProxy)

