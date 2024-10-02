_STATE_TO_BE_LOADED = True


def load_appsec():
    """Lazily load the appsec module listeners."""
    from ddtrace.appsec._asm_request_context import listen

    global _STATE_TO_BE_LOADED
    if _STATE_TO_BE_LOADED:
        listen()
        _STATE_TO_BE_LOADED = False
