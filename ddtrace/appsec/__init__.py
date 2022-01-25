from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

_appsec_import_error = None

try:
    from ddtrace.appsec.processor import AppSecSpanProcessor
except ImportError as e:
    from ddtrace import config

    AppSecSpanProcessor = None  # type: ignore
    _appsec_import_error = repr(e)

    # DDAS-001-01
    log.error(
        "[DDAS-001-01] "
        "AppSec could not start because of an unexpected error. No security activities will be collected. "
        "Please contact support at https://docs.datadoghq.com/help/ for help. Error details: \n%s",
        _appsec_import_error,
    )

    if config._raise:
        raise


__all__ = ["AppSecSpanProcessor"]
