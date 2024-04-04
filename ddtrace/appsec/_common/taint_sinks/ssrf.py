#!/usr/bin/env python3
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)


def wrapped_open_ED4CF71136E15EBF(original_open_callable, instance, args, kwargs):
    """
    wrapper for open url function
    """
    if asm_config._iast_enabled:
        # SSRF sink to be added
        pass

    if asm_config._asm_enabled and asm_config._ep_enabled:
        try:
            from ddtrace.appsec._asm_request_context import call_waf_callback
            from ddtrace.appsec._asm_request_context import in_context
        except ImportError:
            # open is used during module initialization
            # and shouldn't be changed at that time
            return original_open_callable(*args, **kwargs)

        url = args[0] if args else kwargs.get("fullurl", None)
        if url and in_context():
            if not (url.startswith("http://") or url.startswith("https://")):
                url = "http://" + url  # + "/latest/user-data"
            call_waf_callback({"SSRF_ADDRESS": url}, crop_trace="wrapped_open_ED4CF71136E15EBF")
            # DEV: Next part of the exploit prevention feature: add block here
    return original_open_callable(*args, **kwargs)


def patch():
    # type: () -> None
    """Wrap hashing functions.
    Weak hashing algorithms are those that have been proven to be of high risk, or even completely broken,
    and thus are not fit for use.
    """
    from ddtrace.appsec._iast._patch import set_and_check_module_is_patched
    from ddtrace.appsec._common_module_patches import try_wrap_function_wrapper

    if not set_and_check_module_is_patched("urllib", default_attr="_datadog_ssrf_patch"):
        return

    try_wrap_function_wrapper("urllib.request", "OpenerDirector.open", wrapped_open_ED4CF71136E15EBF)


def get_version():
    # type: () -> str
    return ""
