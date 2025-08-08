import sys

from ddtrace.internal import core
from ddtrace.settings.asm import config as asm_config


_APPSEC_TO_BE_LOADED = True


def _asm_switch_state() -> None:
    if asm_config._asm_enabled:
        from ddtrace.appsec._processor import AppSecSpanProcessor

        AppSecSpanProcessor.enable()
    elif "ddtrace.appsec._processor" in sys.modules:
        from ddtrace.appsec._processor import AppSecSpanProcessor

        AppSecSpanProcessor.disable()


def load_appsec() -> None:
    """Lazily load the appsec module listeners."""
    from ddtrace.appsec._asm_request_context import asm_listen
    from ddtrace.appsec._handlers import listen
    from ddtrace.appsec._trace_utils import listen as trace_listen

    global _APPSEC_TO_BE_LOADED
    if _APPSEC_TO_BE_LOADED:
        listen()
        trace_listen()
        asm_listen()
        core.on("asm.switch_state", _asm_switch_state)
        _APPSEC_TO_BE_LOADED = False
    if asm_config._asm_enabled:
        from ddtrace.appsec._processor import AppSecSpanProcessor

        AppSecSpanProcessor.enable()


def load_common_appsec_modules():
    """Lazily load the common module patches."""
    from ddtrace.settings.asm import config as asm_config

    if asm_config._load_modules:
        from ddtrace.appsec._common_module_patches import patch_common_modules

        patch_common_modules()


# for tests that needs to load the appsec module later
core.on("test.config.override", load_common_appsec_modules)
