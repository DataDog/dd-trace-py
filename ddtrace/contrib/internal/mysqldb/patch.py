import os
from typing import Dict

import MySQLdb
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.dbapi import TracedConnection
from ddtrace.contrib.internal.trace_utils import _convert_to_string
from ddtrace.contrib.internal.trace_utils import ext_service
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.ext import net
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_database_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.propagation._database_monitoring import _DBM_Propagator
from ddtrace.settings.asm import config as asm_config
from ddtrace.trace import Pin


config._add(
    "mysqldb",
    dict(
        _default_service=schematize_service_name("mysql"),
        _dbapi_span_name_prefix="mysql",
        _dbapi_span_operation_name=schematize_database_operation("mysql.query", database_provider="mysql"),
        trace_fetch_methods=asbool(os.getenv("DD_MYSQLDB_TRACE_FETCH_METHODS", default=False)),
        trace_connect=asbool(os.getenv("DD_MYSQLDB_TRACE_CONNECT", default=False)),
        _dbm_propagator=_DBM_Propagator(0, "query"),
    ),
)

KWPOS_BY_TAG = {
    net.TARGET_HOST: ("host", 0),
    net.SERVER_ADDRESS: ("host", 0),
    db.USER: ("user", 1),
    db.NAME: ("db", 3),
}


def get_version():
    # type: () -> str
    return ".".join(map(str, MySQLdb.version_info[0:3]))


def _supported_versions() -> Dict[str, str]:
    return {"mysqldb": "*"}


def patch():
    # patch only once
    if getattr(MySQLdb, "_datadog_patch", False):
        return
    MySQLdb._datadog_patch = True

    Pin().onto(MySQLdb)

    # `Connection` and `connect` are aliases for
    # `Connect`; patch them too
    _w("MySQLdb", "Connect", _connect)
    if hasattr(MySQLdb, "Connection"):
        _w("MySQLdb", "Connection", _connect)
    if hasattr(MySQLdb, "connect"):
        _w("MySQLdb", "connect", _connect)

    if asm_config._iast_enabled:
        from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_sink
        from ddtrace.appsec._iast.constants import VULN_SQL_INJECTION

        _set_metric_iast_instrumented_sink(VULN_SQL_INJECTION)


def unpatch():
    if not getattr(MySQLdb, "_datadog_patch", False):
        return
    MySQLdb._datadog_patch = False

    pin = Pin.get_from(MySQLdb)
    if pin:
        pin.remove_from(MySQLdb)

    # unpatch MySQLdb
    _u(MySQLdb, "Connect")
    if hasattr(MySQLdb, "Connection"):
        _u(MySQLdb, "Connection")
    if hasattr(MySQLdb, "connect"):
        _u(MySQLdb, "connect")


def _connect(func, instance, args, kwargs):
    pin = Pin.get_from(MySQLdb)

    if not pin or not pin.enabled() or not config.mysqldb.trace_connect:
        conn = func(*args, **kwargs)
    else:
        with pin.tracer.trace(
            "MySQLdb.connection.connect", service=ext_service(pin, config.mysqldb), span_type=SpanTypes.SQL
        ) as span:
            span.set_tag_str(COMPONENT, config.mysqldb.integration_name)

            # set span.kind to the type of operation being performed
            span.set_tag_str(SPAN_KIND, SpanKind.CLIENT)

            span.set_tag(_SPAN_MEASURED_KEY)
            conn = func(*args, **kwargs)
    return patch_conn(conn, *args, **kwargs)


def patch_conn(conn, *args, **kwargs):
    tags = {
        t: _convert_to_string(kwargs[k]) if k in kwargs else _convert_to_string(args[p])
        for t, (k, p) in KWPOS_BY_TAG.items()
        if k in kwargs or len(args) > p
    }
    tags[db.SYSTEM] = "mysql"
    tags[net.TARGET_PORT] = conn.port
    pin = Pin(tags=tags)

    # grab the metadata from the conn
    wrapped = TracedConnection(conn, pin=pin, cfg=config.mysqldb)
    pin.onto(wrapped)
    return wrapped
