import sys
import traceback
import ddtrace
import importlib

import typing as t
from ddtrace.settings.error_reporting import _er_config


_internal_debug_logger = None

if _er_config._internal_logger:
    _debug_logger_path: str = _er_config._internal_logger
    logger_path, logger_name = _debug_logger_path.rsplit('.', 1)
    module = importlib.import_module(logger_path)
    _internal_debug_logger = getattr(module, logger_name)


def _default_datadog_exc_callback(*args, exc=None):
    if not exc:
        _, exc, _ = sys.exc_info()
    if not exc:
        return

    span = ddtrace.tracer.current_span()
    if not span:
        return

    span._add_event(
        "exception",
        {"message": str(exc), "type": type(exc).__name__, "stack": "".join(traceback.format_exception(exc))},
    )

    if _internal_debug_logger:
        _internal_debug_logger.exception("Handled exception")
