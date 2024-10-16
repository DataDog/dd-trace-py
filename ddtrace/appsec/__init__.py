_APPSEC_TO_BE_LOADED = True


def load_appsec():
    """Lazily load the appsec module listeners."""
    from ddtrace.appsec._asm_request_context import listen

    global _APPSEC_TO_BE_LOADED
    if _APPSEC_TO_BE_LOADED:
        listen()
        _APPSEC_TO_BE_LOADED = False
