import atexit
import sys

import mlflow
from mlflow.tracking import fluent as mlflow_fluent
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace._trace.span import Span
from ddtrace.constants import _HOSTNAME_KEY
from ddtrace.contrib.internal.mlflow.constants import LOG_ATTR_MLFLOW_RUN_ID
from ddtrace.contrib.internal.mlflow.constants import MLFLOW_RUN_ID_TAG
from ddtrace.contrib.internal.mlflow.constants import MLflowLogType
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.hostname import get_hostname
from ddtrace.internal.telemetry import get_config as _get_config
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import get_llmobs_tags


MLFLOW_ACTIVE_RUN_SPANS = {}
MLFLOW_ACTIVE_STEP_SPANS = {}
MLFLOW_CURRENT_STEPS = {}
_MLFLOW_ATEXIT_REGISTERED = False

config._add(
    "mlflow",
    dict(
        trace_run_tags=_get_config("DD_TRACE_MLFLOW_RUN_TAGS", default=False, modifier=asbool),
        log_injection=_get_config(
            "DD_TRACE_MLFLOW_LOGS_INJECTION",
            default=_get_config(
                "DD_MODEL_LAB_ENABLED",
                default=_get_config("DD_MODEL_LAB", default=False, modifier=asbool),
                modifier=asbool,
            ),
            modifier=asbool,
        ),
    ),
)


def get_version() -> str:
    return str(getattr(mlflow, "__version__", ""))


def _supported_versions() -> dict[str, str]:
    return {"mlflow": ">=2.11.4"}


def _finish_unfinished_spans_at_exit():
    # Finish child spans before run spans to preserve parent/child ordering.
    for step_span in list(MLFLOW_ACTIVE_STEP_SPANS.values()):
        if step_span is not None:
            step_span.finish()

    for run_span in list(MLFLOW_ACTIVE_RUN_SPANS.values()):
        if run_span is not None:
            run_span._finish_with_ancestors()

    MLFLOW_ACTIVE_RUN_SPANS.clear()
    MLFLOW_ACTIVE_STEP_SPANS.clear()
    MLFLOW_CURRENT_STEPS.clear()


def _on_span_start(span: Span):
    """
    This function is used to set the MLFlow run id of every span in a run.
    It will not work for the first span of the run as run_id is not active yet.

    Not: this function is tracing specific and should not be in patch.py in the best
    scenario but having it here prevents dispatching another event.
    """
    run_id = getattr(getattr(mlflow.active_run(), "info", None), "run_id", None)
    if not run_id and span._parent:
        # if we enter another thread, active_run() can be None
        run_id = span._parent.get_tag(MLFLOW_RUN_ID_TAG)
    if run_id:
        run_id = str(run_id)
        # sets mlflow.run_id on apm span tags
        span._set_attribute(MLFLOW_RUN_ID_TAG, run_id)
        span._set_attribute(_HOSTNAME_KEY, get_hostname())
        if span.span_type == SpanTypes.LLM:
            # set mlflow.run_id on mlobs span tags
            llmobs_tags = get_llmobs_tags(span) or {}
            if MLFLOW_RUN_ID_TAG not in llmobs_tags:
                _annotate_llmobs_span_data(span, tags={MLFLOW_RUN_ID_TAG: run_id})


def _traced_start_run(wrapped, instance, args, kwargs):
    requested_run_id = get_argument_value(args, kwargs, 0, "run_id", True)
    if requested_run_id is not None and requested_run_id in MLFLOW_ACTIVE_RUN_SPANS:
        # it is possible to restart a run in mlflow, we are not supporting it right now
        return wrapped(*args, **kwargs)

    experiment_id = get_argument_value(args, kwargs, 1, "experiment_id", True)
    run_name = get_argument_value(args, kwargs, 2, "run_name", True)
    parent_run_id = get_argument_value(args, kwargs, 4, "parent_run_id", True)
    run_tags = get_argument_value(args, kwargs, 5, "tags", True) or {}

    with core.context_with_data(
        "mlflow.run",
        span_name="run",
        service=config.mlflow.get("service"),
        span_type=SpanTypes.WORKER,
    ) as ctx:
        run = wrapped(*args, **kwargs)
        run_id = run.info.run_id

        MLFLOW_CURRENT_STEPS[run_id] = 0
        # we use a different event than mflow.run because we cannot know the run_id before
        core.dispatch(
            "mlflow.new.run", (ctx, run_id, experiment_id, run_name, run_tags, parent_run_id, MLFLOW_ACTIVE_RUN_SPANS)
        )
        core.dispatch("mlflow.new.step", (run_id, MLFLOW_ACTIVE_RUN_SPANS, MLFLOW_ACTIVE_STEP_SPANS))

        return run


def _traced_end_run(wrapped, instance, args, kwargs):
    active_run = mlflow.active_run()

    # when using with mlflow.start_run, _traced_end_run will be called
    # multiple times by mlfow
    if active_run is None:
        return wrapped(*args, **kwargs)

    run_id = active_run.info.run_id

    # if an error happened during the run, it will be captured here
    run_exc_info = sys.exc_info()
    end_run_exc_info = None

    try:
        return wrapped(*args, **kwargs)
    except Exception:
        # error happening when trying to end the run
        end_run_exc_info = sys.exc_info()
        raise
    finally:
        step_id = MLFLOW_CURRENT_STEPS.pop(run_id, 0)
        core.dispatch(
            "mlflow.end.run",
            (run_id, step_id, MLFLOW_ACTIVE_RUN_SPANS, MLFLOW_ACTIVE_STEP_SPANS, run_exc_info, end_run_exc_info),
        )


def _traced_log_param(wrapped, instance, args, kwargs):
    run_id = getattr(getattr(mlflow.active_run(), "info", None), "run_id", None)
    if not run_id:
        return wrapped(*args, **kwargs)

    key = get_argument_value(args, kwargs, 0, "key")
    value = get_argument_value(args, kwargs, 1, "value")

    core.dispatch("mlflow.log", (run_id, MLflowLogType.PARAMS, MLFLOW_ACTIVE_STEP_SPANS, (key, value)))

    return wrapped(*args, **kwargs)


def _traced_log_metric(wrapped, instance, args, kwargs):
    run_id = get_argument_value(args, kwargs, 5, "run_id", True)
    if run_id:
        # if the user is providing a run_id, we should check we have a span for it
        if run_id not in MLFLOW_ACTIVE_RUN_SPANS:
            return wrapped(*args, **kwargs)
    else:
        run_id = getattr(getattr(mlflow.active_run(), "info", None), "run_id", None)
        if not run_id:
            return wrapped(*args, **kwargs)

    key = get_argument_value(args, kwargs, 0, "key")
    value = get_argument_value(args, kwargs, 1, "value")
    step = get_argument_value(args, kwargs, 2, "step", True)

    core.dispatch("mlflow.log", (run_id, MLflowLogType.METRICS, MLFLOW_ACTIVE_STEP_SPANS, (key, value)))

    if step is not None:
        MLFLOW_CURRENT_STEPS[run_id] = step
        core.dispatch("mlflow.end.step", (run_id, step, MLFLOW_ACTIVE_STEP_SPANS))
        core.dispatch("mlflow.new.step", (run_id, MLFLOW_ACTIVE_RUN_SPANS, MLFLOW_ACTIVE_STEP_SPANS))

    return wrapped(*args, **kwargs)


def _mlflow_log_correlation_context(log_context):
    if not config.mlflow.get("log_injection", True):
        return
    log_context[LOG_ATTR_MLFLOW_RUN_ID] = getattr(getattr(mlflow.active_run(), "info", None), "run_id", None)


def patch():
    global _MLFLOW_ATEXIT_REGISTERED

    if getattr(mlflow, "_datadog_patch", False):
        return

    mlflow._datadog_patch = True

    _w(mlflow, "start_run", _traced_start_run)
    # When logging a metric, if no run is active, MLFlow
    # can automatically create a new run using this
    # method
    _w(mlflow_fluent, "start_run", _traced_start_run)

    _w(mlflow, "end_run", _traced_end_run)
    # When using with mlflow.start_run, context manager exit will trigger
    # mlflow_fluent.end_run and not mlflow.end_run
    _w(mlflow_fluent, "end_run", _traced_end_run)

    _w(mlflow, "log_metric", _traced_log_metric)
    _w(mlflow, "log_param", _traced_log_param)

    core.on("trace.log_correlation_context", _mlflow_log_correlation_context)
    core.on("trace.span_start", _on_span_start)

    if not _MLFLOW_ATEXIT_REGISTERED:
        atexit.register(_finish_unfinished_spans_at_exit)
        _MLFLOW_ATEXIT_REGISTERED = True


def unpatch():
    if not getattr(mlflow, "_datadog_patch", False):
        return

    mlflow._datadog_patch = False

    _u(mlflow, "start_run")
    _u(mlflow, "end_run")
    _u(mlflow, "log_metric")
    _u(mlflow, "log_param")
    _u(mlflow_fluent, "end_run")
    _u(mlflow_fluent, "start_run")

    core.reset_listeners("trace.log_correlation_context", _mlflow_log_correlation_context)
    core.reset_listeners("trace.span_start", _on_span_start)
