#!/usr/bin/env python3
import json.encoder

import wrapt

from ddtrace.appsec._iast._taint_utils import LazyTaintDict


def patch():
    if getattr(json.encoder, "_datadog_patch", False):
        return
    json.encoder_datadog_patch = True

    wrapt.wrap_function_wrapper("json.encoder", "JSONEncoder.default", patched_json_encoder_default)


def patched_json_encoder_default(original_func, instance, args, kwargs):
    if isinstance(args[0], LazyTaintDict):
        return args[0]._obj

    return original_func(*args, **kwargs)


def get_version():
    # type: () -> str
    return ""
