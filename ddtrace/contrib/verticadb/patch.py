import vertica_python

from ddtrace import config

from .connection import TracedVerticaConnection as TracedConnection

_Connection = vertica_python.Connection
_connect = vertica_python.connect


def meta_execute(instance, span, conf, *args, **kwargs):
    span.set_tag("query", args[0])
    return


# tracing configuration
config._add(
    "vertica",
    {
        "service_name": "vertica",
        "patch": {
            "Cursor": {
                "routines": {
                    "execute": {
                        "operation_name": "vertica.query",
                        "span_type": "vertica",
                        "meta_routine": meta_execute,
                    },
                },
            },
        },
    },
)


def patch():
    if getattr(vertica_python.Connection, "_datadog_patch", False):
        return

    setattr(vertica_python, "Connection", TracedConnection)
    setattr(vertica_python, "connect", vertica_python.Connection)
    setattr(vertica_python.Connection, "_datadog_patch", True)


def unpatch():
    if not getattr(vertica_python.connect, "_datadog_patch", False):
        return

    setattr(vertica_python, "Connection", _Connection)
    setattr(vertica_python, "connect", _connect)
