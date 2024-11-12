_APPSEC_TO_BE_LOADED = True
_IAST_TO_BE_LOADED = True


def load_appsec():
    """Lazily load the appsec module listeners."""
    from ddtrace.appsec._asm_request_context import asm_listen

    global _APPSEC_TO_BE_LOADED
    if _APPSEC_TO_BE_LOADED:
        asm_listen()
        _APPSEC_TO_BE_LOADED = False


def load_iast():
    """Lazily load the iast module listeners."""
    from ddtrace.appsec._iast._iast_request_context import iast_listen

    global _IAST_TO_BE_LOADED
    if _IAST_TO_BE_LOADED:
        iast_listen()
        _IAST_TO_BE_LOADED = False
