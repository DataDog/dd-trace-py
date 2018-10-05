import sys

import wrapt
import vertica_python

import ddtrace
from ddtrace import Pin
from ddtrace.compat import reraise
from ddtrace.pin import _DD_PIN_NAME, _DD_PIN_PROXY_NAME

from ...ext import db as dbx

_Cursor = vertica_python.vertica.cursor.Cursor


def execute_meta(instance, meta, *args, **kwargs):
    """A generator that allows complete customization of a trace.
    """
    # send the operation_name specified by the config so the span can be
    # created with it
    span = yield meta["operation_name"]
    # set default tags
    if "span_type" in meta:
        span.span_type = meta["span_type"]
    meta_routine = meta["meta_routine"]
    meta_routine(instance, span, meta, *args, **kwargs)
    yield span


class TracedVerticaCursor(wrapt.ObjectProxy):
    """TODO: this class is _almost_ abstract enough to be used for any integration.

    Keeping it as a vertica specific implementation to minimize the POC complexity.
    """
    def __init__(self, instance, pin):
        super(TracedVerticaCursor, self).__init__(instance)

        pin.onto(self)
        conf = pin._config["patch"][self.__wrapped__.__class__.__name__]

        for routine_name in conf['routines']:
            # get the tracing metadata specified for this routine
            meta = conf["routines"][routine_name]

            def traced_routine(wrapped, instance, args, kwargs):
                pin = Pin.get_from(self)
                gen = execute_meta(self, meta, *args, **kwargs)
                operation_name = gen.send(None)
                tracer = pin.tracer # or ddtrace.tracer
                with tracer.trace(operation_name, service=pin.service) as span:
                    span.set_tags(pin.tags)
                    # send back the span for tags to be set
                    gen.send(span)

                    try:
                        # invoke the attribute
                        result = wrapped(*args, **kwargs)
                        return result
                    finally:
                        span.set_metric(dbx.ROWCOUNT, self.rowcount)

            # proxy each routine
            wrapt.wrap_function_wrapper(instance, routine_name, traced_routine)


'''
# Keep the old POC around for comparison

class TracedVerticaCursor(wrapt.ObjectProxy):
    def __init__(self, instance):
        super(TracedVerticaCursor, self).__init__(instance)
     def __getattribute__(self, name):
        # prevent an infinite loop when trying to access the pin
        if name not in [_DD_PIN_NAME, _DD_PIN_PROXY_NAME]:
            attr = wrapt.ObjectProxy.__getattribute__(self, name)
        else:
            return object.__getattribute__(self, name)
         pin = Pin.get_from(self)
        conf = pin._config['cursor']
         # name = "{0}.{1}".format(attr.__module__, attr.__name__)
        name = attr.__name__
        if hasattr(attr, "__call__") and name in conf['routines']:
            tracer = pin.tracer
             # get the tracing metadata specified for this routine
            meta = conf['routines'][attr.__name__]
             def traced_routine(*args, **kwargs):
                gen = execute_meta(self, meta, *args, **kwargs)
                operation_name = gen.send(None)
                with tracer.trace(operation_name, service=pin.service) as span:
                    span.set_tags(pin.tags)
                    # send back the span for tags to be set
                    gen.send(span)
                     try:
                        # invoke the attribute
                        result = attr(*args, **kwargs)
                        return result
                    finally:
                        span.set_metric(dbx.ROWCOUNT, self.rowcount)
            return traced_routine
        else:
            return attr
'''
