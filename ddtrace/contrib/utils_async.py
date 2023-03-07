from ddtrace import Pin
from ddtrace import config
from ddtrace.constants import SPAN_KIND
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.contrib import dbapi_async
from ddtrace.contrib.trace_utils import ext_service
from ddtrace.contrib.utils import patch_conn
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.internal.constants import COMPONENT


class PsycopgTracedAsyncCursor(dbapi_async.TracedAsyncCursor):
    def __init__(self, cursor, pin, cfg, *args, **kwargs):
        super(PsycopgTracedAsyncCursor, self).__init__(cursor, pin, cfg)

    async def __aenter__(self):
        # previous versions of the dbapi didn't support context managers. let's
        # reference the func that would be called to ensure that errors
        # messages will be the same.
        await self.__wrapped__.__aenter__()

        # and finally, yield the traced cursor.
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        # previous versions of the dbapi didn't support context managers. let's
        # reference the func that would be called to ensure that errors
        # messages will be the same.
        await self.__wrapped__.__aexit__(exc_type, exc_val, exc_tb)

        # and finally, yield the traced cursor.
        return self


class PsycopgFetchTracedAsyncCursor(PsycopgTracedAsyncCursor, dbapi_async.FetchTracedAsyncCursor):
    """PsycopgFetchTracedAsyncCursor for psycopg"""


class PsycopgTracedAsyncConnection(dbapi_async.TracedAsyncConnection):
    def __init__(self, conn, pin=None, cursor_cls=None):
        package = conn.__class__.__module__.split(".")[0]
        if not cursor_cls:
            # Do not trace `fetch*` methods by default
            cursor_cls = (
                PsycopgFetchTracedAsyncCursor
                if config._config[package].trace_fetch_methods
                else PsycopgTracedAsyncCursor
            )

        super(PsycopgTracedAsyncConnection, self).__init__(conn, pin, config._config[package], cursor_cls=cursor_cls)


async def patched_connect_async(connect_func, _, args, kwargs):
    if kwargs.get("traced_conn_cls", None):
        traced_conn_cls = kwargs.get("traced_conn_cls")
        kwargs.pop("traced_conn_cls")
    else:
        traced_conn_cls = PsycopgTracedAsyncConnection

    _config = globals()["config"]._config
    module_name = (
        connect_func.__module__
        if len(connect_func.__module__.split(".")) == 1
        else connect_func.__module__.split(".")[0]
    )
    pin = Pin.get_from(_config[module_name].base_module)

    if not pin or not pin.enabled() or not pin._config.trace_connect:
        conn = await connect_func(*args, **kwargs)
    else:
        with pin.tracer.trace(
            "{}.{}".format(connect_func.__module__, connect_func.__name__),
            service=ext_service(pin, pin._config),
            span_type=SpanTypes.SQL,
        ) as span:
            span.set_tag_str(SPAN_KIND, SpanKind.CLIENT)
            span.set_tag_str(COMPONENT, pin._config.integration_name)
            if span.get_tag(db.SYSTEM) is None:
                span.set_tag_str(db.SYSTEM, pin._config.dbms_name)

            span.set_tag(SPAN_MEASURED_KEY)
            conn = await connect_func(*args, **kwargs)

    return patch_conn(conn, pin=pin, traced_conn_cls=traced_conn_cls)
