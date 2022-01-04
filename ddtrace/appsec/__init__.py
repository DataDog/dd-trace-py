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
        # DDAS-001-01
        log.error(
            "AppSec could not start because of an unexpected error. No security activities will be collected. "
            "Please contact support at https://docs.datadoghq.com/help/ for help.Error details: \n%s",
            _appsec_import_error,
        )
        return
    AppSecProcessor.enable(*args, **kwargs)


def disable():
    if AppSecProcessor is None:
        return
    AppSecProcessor.disable()


__all__ = ["AppSecProcessor", "enable", "disable"]
