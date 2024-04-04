#!/usr/bin/env python3
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)


def wrapped_open_CFDDB7ABBA9081B6(original_open_callable, instance, args, kwargs):
    """
    wrapper for open file function
    """
    if asm_config._iast_enabled:
        from ddtrace.appsec._iast.taint_sinks.path_traversal import check_and_report_path_traversal

        check_and_report_path_traversal(*args, **kwargs)

    if asm_config._asm_enabled and asm_config._ep_enabled:
        try:
            from ddtrace.appsec._asm_request_context import call_waf_callback
            from ddtrace.appsec._asm_request_context import in_context
        except ImportError:
            # open is used during module initialization
            # and shouldn't be changed at that time
            return original_open_callable(*args, **kwargs)

        filename = args[0] if args else kwargs.get("file", None)
        if filename and in_context():
            call_waf_callback({"LFI_ADDRESS": filename}, crop_trace="wrapped_open_CFDDB7ABBA9081B6")
            # DEV: Next part of the exploit prevention feature: add block here
    return original_open_callable(*args, **kwargs)
