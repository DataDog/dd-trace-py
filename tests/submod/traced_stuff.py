# -*- encoding: utf-8 -*-
from ddtrace.ext import SpanTypes


def inner():
    return 42


def traceme():
    cake = "🍰"  # noqa
    return 42 + inner()


def exit_call(tracer):
    with tracer.trace("exit", span_type=SpanTypes.HTTP):
        return 42


def middle(tracer):
    with tracer.trace("middle"):
        return exit_call(tracer)


def entrypoint(tracer):
    return middle(tracer)


def traced_entrypoint(tracer):
    with tracer.trace("entry"):
        return entrypoint(tracer)
