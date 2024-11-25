from ddtrace.internal import core
from ddtrace.settings.asm import config as asm_config


_APPSEC_TO_BE_LOADED = True


def load_appsec():
    """Lazily load the appsec module listeners."""
    from ddtrace.appsec._asm_request_context import listen

    global _APPSEC_TO_BE_LOADED
    if _APPSEC_TO_BE_LOADED:
        listen()
        _APPSEC_TO_BE_LOADED = False


def load_iast():
    """Lazily load the iast module listeners."""
    from ddtrace.appsec._iast._iast_request_context import iast_listen

    global _IAST_TO_BE_LOADED
    if _IAST_TO_BE_LOADED:
        iast_listen()
        _IAST_TO_BE_LOADED = False


def load_common_appsec_modules():
    """Lazily load the common module patches."""
    if (asm_config._ep_enabled and asm_config._asm_enabled) or asm_config._iast_enabled:
        from ddtrace.appsec._common_module_patches import patch_common_modules

        patch_common_modules()


# for tests that needs to load the appsec module later
core.on("test.config.override", load_common_appsec_modules)
