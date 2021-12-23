from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

_appsec_import_error = None

try:
    from ddtrace.appsec.processor import AppSecProcessor
except ImportError as e:
    from ddtrace import config

    if config._raise:
        raise

    AppSecProcessor = None  # type: ignore
    _appsec_import_error = repr(e)


def enable(*args, **kwargs):
    if AppSecProcessor is None:
        log.warning("AppSec was enabled but couldn't start due to the following error:\n%s", _appsec_import_error)
        return
    AppSecProcessor.enable(*args, **kwargs)


def disable():
    if AppSecProcessor is None:
        return
    AppSecProcessor.disable()


__all__ = ["AppSecProcessor", "enable", "disable"]
