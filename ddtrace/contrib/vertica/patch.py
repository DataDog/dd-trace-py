import importlib
import wrapt

import ddtrace
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

_PATCHED = False

def copy_before(instance, span, conf, *args, **kwargs):
    span.set_tag("query", args[0])

def execute_before(instance, span, conf, *args, **kwargs):
    span.set_tag("query", args[0])

def execute_after(result, instance, span, conf, *args, **kwargs):
    span.set_metric(dbx.ROWCOUNT, instance.rowcount)

def fetch_after(result, instance, span, conf, *args, **kwargs):
    span.set_metric(dbx.ROWCOUNT, instance.rowcount)

def cursor_after(cursor, instance, span, conf, *args, **kwargs):
    tags = {}
    tags[net.TARGET_HOST] = instance.options["host"]
    tags[net.TARGET_PORT] = instance.options["port"]
    if "user" in instance.options:
        tags[dbx.USER] = instance.options["user"]
    if "database" in instance.options:
        tags[dbx.NAME] = instance.options["database"]

    pin = Pin(
        service=config.vertica["service_name"],
        app=APP,
        app_type=AppTypes.db,
        tags=tags,
        _config=config.vertica["patch"]["vertica_python.vertica.cursor.Cursor"]
    )
    pin.onto(cursor)


# tracing configuration
config._add(
    "vertica",
    {
        "service_name": "vertica",
        "app": "vertica",
        "app_type": "db",
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
                        "on_before": execute_before,
                        "on_after": execute_after,
                    },
                    "copy": {
                        "operation_name": "vertica.copy",
                        "span_type": "vertica",
                        "on_before": copy_before,
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
                    "nextset": {
                        "operation_name": "vertica.nextset",
                        "span_type": "vertica",
                        "on_after": fetch_after,
                    },
                },
            },
        },
    },
)

def patch():
    global _PATCHED
    if _PATCHED:
        return

    _install(config.vertica)
    _PATCHED = True


def unpatch():
    global _PATCHED
    if _PATCHED:
        _uninstall(config.vertica)
        _PATCHED = False

def _uninstall(config):
    for patch_class_path in config["patch"]:
        patch_mod = '.'.join(patch_class_path.split('.')[0:-1])
        patch_class = patch_class_path.split('.')[-1]

        for patch_routine in config["patch"][patch_class_path]["routines"]:
            mod = importlib.import_module(patch_mod)
            cls = getattr(mod, patch_class)
            unwrap(cls, patch_routine)

def _install(config):
    for patch_class_path in config["patch"]:
        patch_mod = '.'.join(patch_class_path.split('.')[0:-1])
        patch_class = patch_class_path.split('.')[-1]

        for patch_routine in config["patch"][patch_class_path]["routines"]:
            def _wrap():
                # _wrap is needed to provide data to the wrappers which can
                # only be provided from a function closure.

                _patch_routine = patch_routine
                _patch_item = patch_class_path

                # patch the __init__ of the class with a Pin instance containing the defaults
                @wrapt.patch_function_wrapper(patch_mod, "{}.{}".format(patch_class, "__init__"))
                def init_wrapper(wrapped, instance, args, kwargs):
                    wrapped(*args, **kwargs)

                    # create and attach a pin with the defaults
                    Pin(
                        service=config["service_name"],
                        app=config["app"],
                        app_type=config["app_type"],
                        tags=config.get("tags", {}),
                        tracer=config.get("tracer", ddtrace.tracer),
                        _config=config["patch"][_patch_item],
                    ).onto(instance)

                def _find_config(instance, routine_name):
                    bases = instance.__class__.__bases__

                    while bases:
                        newbases = []
                        for base in bases:
                            full_name = "{}.{}".format(base.__module__, base.__name__)
                            if full_name in config["patch"] and routine_name in config["patch"][full_name]["routines"]:
                                return config["patch"][full_name]["routines"][routine_name]
                            newbases += base.__class__.__bases__

                @wrapt.patch_function_wrapper(patch_mod, "{}.{}".format(patch_class, patch_routine))
                def wrapper(wrapped, instance, args, kwargs):
                    # TODO?: remove Pin dependence
                    pin = Pin.get_from(instance)

                    # TODO: inheritance problem with pins:
                    # Say we have a base class B and child class C:
                    # class B:
                    #   def my_method(self):
                    #     pass
                    #
                    # class C(B):
                    #   pass
                    #
                    # with tracing config:
                    #  'patch': {
                    #     'B': {
                    #       'routines': {
                    #         'my_method: {...}
                    #       }
                    #     },
                    #     'C': {}  # does not contain 'my_method'
                    #  }
                    # and an instance `c`:
                    # c = C()
                    # c.my_method()
                    #
                    # An instance of either will have a pin attached so that
                    # the user can override specific configuration for that
                    # instance.
                    #
                    # The pins will contain the config for the instance which
                    # they are attached:
                    #   PinB._config == { 'routines: { 'my_method: { ... } } }
                    #   PinC._config == { }
                    #
                    # The problem here is that at this point in the wrapper
                    # when c.my_method() is called we have an instance of C and
                    # a pin from C, but require the config from B.
                    #
                    # Possible solution: follow inheritance and look for config
                    # on a parent. Can we use normal python inheritance somehow?
                    if _patch_routine in pin._config["routines"]:
                        conf = pin._config["routines"][_patch_routine]
                    else:
                        conf = _find_config(instance, _patch_routine)

                    enabled = conf.get("trace_enabled", True)

                    span = None

                    try:
                        # shortcut if not enabled
                        if not enabled:
                            result = wrapped(*args, **kwargs)
                            return result

                        operation_name = conf["operation_name"]
                        tracer = pin.tracer
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
