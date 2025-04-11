# this module must not load any other unsafe appsec module directly

from ddtrace.internal import core


_APPSEC_TO_BE_LOADED = True


def load_appsec():
    """Lazily load the appsec module listeners."""
    from ddtrace.appsec._asm_request_context import asm_listen

    global _APPSEC_TO_BE_LOADED
    if _APPSEC_TO_BE_LOADED:
        asm_listen()
        _APPSEC_TO_BE_LOADED = False


def load_common_appsec_modules():
    """Lazily load the common module patches."""
    from ddtrace.settings.asm import config as asm_config

    if asm_config._load_modules:
        from ddtrace.appsec._common_module_patches import patch_common_modules

        patch_common_modules()


# for tests that needs to load the appsec module later
core.on("test.config.override", load_common_appsec_modules)
