_APPSEC_TO_BE_LOADED = True


def load_appsec():
    """Lazily load the appsec module listeners."""
    from ddtrace.appsec._asm_request_context import asm_listen
    from ddtrace.appsec._iast._iast_request_context import iast_listen

    global _APPSEC_TO_BE_LOADED
    if _APPSEC_TO_BE_LOADED:
        asm_listen()
        iast_listen()
        _APPSEC_TO_BE_LOADED = False
