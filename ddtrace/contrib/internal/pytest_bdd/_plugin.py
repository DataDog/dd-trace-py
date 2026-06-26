import json

from ddtrace.contrib.internal.pytest_bdd.patch import get_version  # noqa: F401
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def _extract_span(item):
    """Extract span from `step_func`."""
    return getattr(item, "_datadog_span", None)


def _store_span(item, span):
    """Store span at `step_func`."""
    item._datadog_span = span


def _extract_step_func_args(step, step_func, step_func_args):
    """Backwards-compatible get arguments from step_func or step_func_args"""
    if not (hasattr(step_func, "parser") or hasattr(step_func, "_pytest_bdd_parsers")):
        return step_func_args

    # store parsed step arguments
    try:
        parsers = [step_func.parser]
    except AttributeError:
        try:
            # pytest-bdd >= 6.0.0
            parsers = step_func._pytest_bdd_parsers
        except AttributeError:
            parsers = []
    for parser in parsers:
        if parser is not None:
            converters = getattr(step_func, "converters", {})
            parameters = {}
            try:
                for arg, value in parser.parse_arguments(step.name).items():
                    try:
                        if arg in converters:
                            value = converters[arg](value)
                    except Exception:
                        log.debug("argument conversion failed.")
                    parameters[arg] = value
            except Exception:
                log.debug("argument parsing failed.")

    return parameters or None


def _get_step_func_args_json(step, step_func, step_func_args):
    """Get step function args as JSON, catching serialization errors"""
    try:
        extracted_step_func_args = _extract_step_func_args(step, step_func, step_func_args)
        if extracted_step_func_args:
            return json.dumps(extracted_step_func_args)
        return None
    except TypeError as err:
        log.debug("Could not serialize arguments", exc_info=True)
        return json.dumps({"error_serializing_args": str(err)})
