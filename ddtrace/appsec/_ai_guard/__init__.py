import ddtrace.internal.logger as ddlogger
from ddtrace.settings.asm import ai_guard_config


logger = ddlogger.get_logger(__name__)

_AI_GUARD_TO_BE_LOADED: bool = True


def init_ai_guard():
    """Lazily load the ai_guard module listeners."""
    global _AI_GUARD_TO_BE_LOADED
    if _AI_GUARD_TO_BE_LOADED:
        try:
            if not ai_guard_config._ai_guard_enabled:
                return

            from ddtrace.appsec._ai_guard._listener import ai_guard_listen

            ai_guard_listen()

        except Exception:
            logger.warning("AI Guard initialization failed", exc_info=True)

        finally:
            _AI_GUARD_TO_BE_LOADED = False
