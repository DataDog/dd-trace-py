from typing import Dict

import emoji
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.wrappers import unwrap as _u


config._add(
    "emoji",
    dict(
        _default_service=schematize_service_name("emoji"),
    ),
)


def get_version() -> str:
    return getattr(emoji, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"emoji": ">=2.14.1"}


def emojize_tags(kwargs):
    return {"foo": "bar"}


def traced_emojize(func, instance, args, kwargs):
    with core.context_with_data(
        "emoji.emojize",
        call_trace=False,
        span_name="emojize",
        span_type=SpanTypes.WORKER,
        service=trace_utils.ext_service(None, config.emoji),
        tags=emojize_tags(kwargs),
    ) as ctx:
        result = func(*args, **kwargs)
        core.dispatch("emoji.emojize", (instance, ctx, result))

        return result


def patch():
    if getattr(emoji, "_datadog_patch", False):
        return
    emoji._datadog_patch = True

    _w("emoji", "emojize", traced_emojize)


def unpatch():
    if not getattr(emoji, "_datadog_patch", False):
        return

    emoji._datadog_patch = False

    _u(emoji, "emojize")
