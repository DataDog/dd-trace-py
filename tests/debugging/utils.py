from ddtrace.debugging._expressions import DDExpression
from ddtrace.debugging._expressions import dd_compile
from ddtrace.debugging._probe.model import CaptureLimits
from ddtrace.debugging._probe.model import DEFAULT_PROBE_CONDITION_ERROR_RATE
from ddtrace.debugging._probe.model import DEFAULT_PROBE_RATE
from ddtrace.debugging._probe.model import DEFAULT_SNAPSHOT_PROBE_RATE
from ddtrace.debugging._probe.model import ExpressionTemplateSegment
from ddtrace.debugging._probe.model import LiteralTemplateSegment
from ddtrace.debugging._probe.model import LogFunctionProbe
from ddtrace.debugging._probe.model import LogLineProbe
from ddtrace.debugging._probe.model import MetricFunctionProbe
from ddtrace.debugging._probe.model import MetricLineProbe
from ddtrace.debugging._probe.model import ProbeEvaluateTimingForMethod
from ddtrace.debugging._probe.model import SpanFunctionProbe


def compile_template(*args):
    template = ""
    segments = []

    for arg in args:
        if isinstance(arg, str):
            template += arg
            segments.append(LiteralTemplateSegment(arg))
        else:
            template += "{" + arg["dsl"] + "}"
            segments.append(ExpressionTemplateSegment(DDExpression(dsl=arg["dsl"], callable=dd_compile(arg["json"]))))

    return {"template": template, "segments": segments}


def create_probe_defaults(f):
    def _wrapper(*args, **kwargs):
        kwargs.setdefault("tags", dict())
        kwargs.setdefault("version", 0)
        return f(*args, **kwargs)

    return _wrapper


def probe_conditional_defaults(f):
    def _wrapper(*args, **kwargs):
        kwargs.setdefault("condition", None)
        kwargs.setdefault("condition_error_rate", DEFAULT_PROBE_CONDITION_ERROR_RATE)
        return f(*args, **kwargs)

    return _wrapper


def function_location_defaults(f):
    def _wrapper(*args, **kwargs):
        kwargs.setdefault("evaluate_at", ProbeEvaluateTimingForMethod.DEFAULT)
        return f(*args, **kwargs)

    return _wrapper


def log_probe_defaults(f):
    def _wrapper(*args, **kwargs):
        kwargs.setdefault("take_snapshot", False)
        kwargs.setdefault("rate", DEFAULT_PROBE_RATE)
        kwargs.setdefault("limits", CaptureLimits())
        return f(*args, **kwargs)

    return _wrapper


def snapshot_probe_defaults(f):
    def _wrapper(*args, **kwargs):
        kwargs.setdefault("take_snapshot", True)
        kwargs.setdefault("rate", DEFAULT_SNAPSHOT_PROBE_RATE)
        kwargs.setdefault("limits", CaptureLimits())
        kwargs.setdefault("template", "")
        kwargs.setdefault("segments", [])
        return f(*args, **kwargs)

    return _wrapper


def metric_probe_defaults(f):
    def _wrapper(*args, **kwargs):
        kwargs.setdefault("rate", DEFAULT_PROBE_RATE)
        kwargs.setdefault("value", None)
        return f(*args, **kwargs)

    return _wrapper


def span_probe_defaults(f):
    def _wrapper(*args, **kwargs):
        kwargs.setdefault("rate", DEFAULT_PROBE_RATE)
        return f(*args, **kwargs)

    return _wrapper


@create_probe_defaults
@probe_conditional_defaults
@snapshot_probe_defaults
def create_snapshot_line_probe(**kwargs):
    return LogLineProbe(**kwargs)


@create_probe_defaults
@probe_conditional_defaults
@function_location_defaults
@snapshot_probe_defaults
def create_snapshot_function_probe(**kwargs):
    return LogFunctionProbe(**kwargs)


@create_probe_defaults
@probe_conditional_defaults
@log_probe_defaults
def create_log_line_probe(**kwargs):
    return LogLineProbe(**kwargs)


@create_probe_defaults
@probe_conditional_defaults
@function_location_defaults
@log_probe_defaults
def create_log_function_probe(**kwargs):
    return LogFunctionProbe(**kwargs)


@create_probe_defaults
@probe_conditional_defaults
@metric_probe_defaults
def create_metric_line_probe(**kwargs):
    return MetricLineProbe(**kwargs)


@create_probe_defaults
@probe_conditional_defaults
@function_location_defaults
@metric_probe_defaults
def create_metric_function_probe(**kwargs):
    return MetricFunctionProbe(**kwargs)


@create_probe_defaults
@probe_conditional_defaults
@function_location_defaults
@span_probe_defaults
def create_span_function_probe(**kwargs):
    return SpanFunctionProbe(**kwargs)
