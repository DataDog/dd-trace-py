from ddtrace.debugging._probe.model import CaptureLimits
from ddtrace.debugging._probe.model import DEFAULT_PROBE_CONDITION_ERROR_RATE
from ddtrace.debugging._probe.model import DEFAULT_PROBE_RATE
from ddtrace.debugging._probe.model import MetricFunctionProbe
from ddtrace.debugging._probe.model import MetricLineProbe
from ddtrace.debugging._probe.model import SnapshotFunctionProbe
from ddtrace.debugging._probe.model import SnapshotLineProbe


def create_probe_defaults(f):
    def _wrapper(*args, **kwargs):
        kwargs.setdefault("tags", dict())
        kwargs.setdefault("active", True)
        kwargs.setdefault("rate", DEFAULT_PROBE_RATE)
        return f(*args, **kwargs)

    return _wrapper


def probe_conditional_defaults(f):
    def _wrapper(*args, **kwargs):
        kwargs.setdefault("condition", None)
        kwargs.setdefault("condition_error_rate", DEFAULT_PROBE_CONDITION_ERROR_RATE)
        return f(*args, **kwargs)

    return _wrapper


def snapshot_probe_defaults(f):
    def _wrapper(*args, **kwargs):
        kwargs.setdefault("limits", CaptureLimits())
        return f(*args, **kwargs)

    return _wrapper


def metric_probe_defaults(f):
    def _wrapper(*args, **kwargs):
        kwargs.setdefault("value", None)
        return f(*args, **kwargs)

    return _wrapper


@create_probe_defaults
@probe_conditional_defaults
@snapshot_probe_defaults
def create_snapshot_line_probe(**kwargs):
    return SnapshotLineProbe(**kwargs)


@create_probe_defaults
@probe_conditional_defaults
@snapshot_probe_defaults
def create_snapshot_function_probe(**kwargs):
    return SnapshotFunctionProbe(**kwargs)


@create_probe_defaults
@probe_conditional_defaults
@metric_probe_defaults
def create_metric_line_probe(**kwargs):
    return MetricLineProbe(**kwargs)


@create_probe_defaults
@probe_conditional_defaults
@metric_probe_defaults
def create_metric_function_probe(**kwargs):
    return MetricFunctionProbe(**kwargs)
