import wrapt

from ddtrace import config, Pin
from ddtrace.ext import net, AppTypes
from ddtrace.utils.wrappers import unwrap

from .constants import APP
from ...ext import db as dbx

"""
Note: we DO NOT import the library to be patched at all!

What this means is that this approach completely solves our patching problem as
well. wrapt will hook into the module import system for the patches that we
provide.
"""


def execute_before(instance, span, conf, *args, **kwargs):
    span.set_tag("query", args[0])

def execute_error(instance, span, conf, *args, **kwargs):
    pass

def execute_after(result, instance, span, conf, *args, **kwargs):
    span.set_metric(dbx.ROWCOUNT, instance.rowcount)

def fetch_after(result, instance, span, conf, *args, **kwargs):
    span.set_metric(dbx.ROWCOUNT, instance.rowcount)

def cursor_after(cursor, instance, span, conf, *args, **kwargs):
    tags = {}
    tags[net.TARGET_HOST] = instance.options["host"]
    tags[net.TARGET_PORT] = instance.options["port"]
    pin = Pin(
        service=config.vertica["service_name"],
        app=APP,
        app_type=AppTypes.db,
        tags=tags,
    )
    pin.onto(cursor)


# tracing configuration
config._add(
    "vertica",
    {
        "service_name": "vertica",
        "patch": {
            "vertica_python.vertica.connection.Connection": {
                "routines": {
                    "cursor": {
                        "trace_enabled": False,
                        "on_after": cursor_after,
                    }
                },
            },
            "vertica_python.vertica.cursor.Cursor": {
                "routines": {
                    "execute": {
                        "operation_name": "vertica.query",
                        "span_type": "vertica",
                        "trace_enabled": True,
                        # TODO: tracer config
                        # "tracer": Tracer(),
                        # TODO??: before and after can be replaced with a generator
                        "on_before": execute_before,
                        "on_after": execute_after,
                        "on_error": execute_error,
                    },
                    "fetchone": {
                        "operation_name": "vertica.fetchone",
                        "span_type": "vertica",
                        "on_after": fetch_after,
                    },
                    "fetchall": {
                        "operation_name": "vertica.fetchall",
                        "span_type": "vertica",
                        "on_after": fetch_after,
                    },
                },
            },
        },
    },
)

def patch():
    # TODO: set marker for idompotency checking (use config??)
    _install(config.vertica["patch"])


def unpatch():
    # note that we inline import here because we do not want to import the
    # library and find out that we want to patch it but haven't yet
    # installed the hooks!
    import vertica_python
    # TODO: POC of unpatching
    unwrap(vertica_python.vertica.connection.Connection, 'cursor')
    unwrap(vertica_python.vertica.cursor.Cursor, 'execute')
    unwrap(vertica_python.vertica.cursor.Cursor, 'fetchone')
    unwrap(vertica_python.vertica.cursor.Cursor, 'fetchall')


def _install(config):
    for patch_class_path in config:
        patch_mod = '.'.join(patch_class_path.split('.')[0:-1])
        patch_class = patch_class_path.split('.')[-1]

        for patch_routine in config[patch_class_path]["routines"]:
            # log.debug('PATCHING {} {}.{}'.format(patch_mod, patch_class, patch_routine))

            def _wrap():
                # _wrap is needed to provide data to the wrapper which can
                # only be provided from the function closure.
                # Not having the closure will mean that each wrapper will have
                # the same patch_routine and patch_item.

                # We need to copy these items to the _wrap closure for them to
                # be available to the wrapper.
                _patch_routine = patch_routine
                _patch_item = patch_class_path

                @wrapt.patch_function_wrapper(patch_mod, "{}.{}".format(patch_class, patch_routine))
                def wrapper(wrapped, instance, args, kwargs):
                    pin = Pin.get_from(instance)

                    # TODO: allow conf setting by pin._config so that users can specify
                    # a unique config for each instance, else default to the global
                    # conf = pin and pin._config

                    conf = config[_patch_item]["routines"][_patch_routine]
                    enabled = conf.get("trace_enabled", True)

                    span = None

                    try:
                        # shortcut if not enabled
                        if not enabled:
                            result = wrapped(*args, **kwargs)
                            return result

                        operation_name = conf["operation_name"]
                        tracer = pin.tracer  # TODO: get tracer from pin, or config or global
                        with tracer.trace(operation_name, service=pin.service) as span:
                            span.set_tags(pin.tags)
                            if "span_type" in conf:
                                span.span_type = conf["span_type"]

                            if "on_before" in conf:
                                conf["on_before"](instance, span, conf, *args, **kwargs)

                            result = wrapped(*args, **kwargs)
                            return result
                    except Exception:
                        if "on_error" in conf:
                            conf["on_error"](instance, span, conf, *args, **kwargs)
                        raise
                    finally:
                        # if an exception is raised result will not exist
                        if "result" not in locals():
                            result = None
                        if "on_after" in conf:
                            conf["on_after"](result, instance, span, conf, *args, **kwargs)
            _wrap()
