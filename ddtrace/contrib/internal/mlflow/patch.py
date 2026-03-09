import sys

import mlflow
from mlflow.tracking import fluent as mlflow_fluent
from wrapt import wrap_function_wrapper as _w

import ddtrace
from ddtrace import config
from ddtrace._trace.processor import SpanProcessor
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.internal.mlflow.constants import LOG_ATTR_MLFLOW_RUN_ID
from ddtrace.contrib.internal.mlflow.constants import MLFLOW_EXPERIMENT_ID_TAG
from ddtrace.contrib.internal.mlflow.constants import MLFLOW_RUN_ID_TAG
from ddtrace.contrib.internal.mlflow.constants import MLFLOW_RUN_NAME_TAG
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils import get_argument_value


MLFLOW_ACTIVE_RUN_SPANS = {}
MLFLOW_ACTIVE_STEP_SPANS = {}
MLFLOW_CURRENT_STEPS = {}


class _MLflowRunIDSpanProcessor(SpanProcessor):
    def on_span_start(self, span):
        run_id = getattr(getattr(mlflow.active_run(), "info", None), "run_id", None)
        if run_id:
            span._set_tag_str(MLFLOW_RUN_ID_TAG, str(run_id))

    def on_span_finish(self, span):
        pass


config._add(
    "mlflow",
    {
        "_default_service": schematize_service_name("mlflow"),
    },
)


def get_version() -> str:
    return str(getattr(mlflow, "__version__", ""))


def _supported_versions() -> dict[str, str]:
    return {"mlflow": "*"}


def _traced_start_run(wrapped, instance, args, kwargs):
    experiment_id = get_argument_value(args, kwargs, 1, "experiment_id", True)
    run_name = get_argument_value(args, kwargs, 2, "run_name", True)
    run_tags = get_argument_value(args, kwargs, 5, "tags", True) or {}
    normalized_tags = {COMPONENT: config.mlflow.integration_name, SPAN_KIND: SpanKind.INTERNAL}
    for key, value in run_tags.items():
        try:
            normalized_tags[key] = str(value)
        except Exception:  # nosec B112
            continue

    if experiment_id is not None:
        normalized_tags[MLFLOW_EXPERIMENT_ID_TAG] = str(experiment_id)
    if run_name is not None:
        normalized_tags[MLFLOW_RUN_NAME_TAG] = str(run_name)

    with core.context_with_data(
        "mlflow.run",
        span_name="mflow.run",
        service=config.mlflow.get("service", config.mlflow._default_service),
        span_type=SpanTypes.WORKER,
        tags=normalized_tags,
    ) as ctx:
        run = wrapped(*args, **kwargs)
        run_id = run.info.run_id

        MLFLOW_CURRENT_STEPS[run_id] = 0
        core.dispatch("mlflow.new.run", (ctx, run_id, MLFLOW_ACTIVE_RUN_SPANS))
        core.dispatch("mlflow.new.step", (run_id, MLFLOW_ACTIVE_STEP_SPANS))

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

    core.dispatch("mlflow.log", (run_id, "params", MLFLOW_ACTIVE_STEP_SPANS, (key, value)))

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

    core.dispatch("mlflow.log", (run_id, "metrics", MLFLOW_ACTIVE_STEP_SPANS, (key, value)))

    if step is not None:
        MLFLOW_CURRENT_STEPS[run_id] = step
        core.dispatch("mlflow.end.step", (run_id, step, MLFLOW_ACTIVE_STEP_SPANS))
        core.dispatch("mlflow.new.step", (run_id, MLFLOW_ACTIVE_STEP_SPANS))

    return wrapped(*args, **kwargs)


def _traced_get_log_correlation_context(wrapped, instance, args, kwargs):
    log_context = wrapped(*args, **kwargs)
    log_context[LOG_ATTR_MLFLOW_RUN_ID] = getattr(getattr(mlflow.active_run(), "info", None), "run_id", None)

    return log_context


def patch():
    if getattr(mlflow, "_datadog_patch", False):
        return

    mlflow._datadog_patch = True
    _w(mlflow, "start_run", _traced_start_run)
    _w(mlflow, "end_run", _traced_end_run)

    # When using with mlflow.start_run, context manager exit will trigger
    # mlflow_fluent.end_run and not mlflow.end_run
    _w(mlflow_fluent, "end_run", _traced_end_run)

    _w(mlflow, "log_metric", _traced_log_metric)
    _w(mlflow, "log_param", _traced_log_param)
    _w(ddtrace.tracer, "get_log_correlation_context", _traced_get_log_correlation_context)

    # Tag all spans with run_id
    processor = _MLflowRunIDSpanProcessor()
    processor.register()

    mlflow._datadog_run_id_span_processor = processor


def unpatch():
    if not getattr(mlflow, "_datadog_patch", False):
        return

    mlflow._datadog_patch = False

    _u(mlflow, "start_run")
    _u(mlflow, "end_run")
    _u(mlflow_fluent, "end_run")
    _u(mlflow, "log_metric")
    _u(mlflow, "log_param")
    _u(ddtrace.tracer, "get_log_correlation_context")

    # Remove processor
    processor = getattr(mlflow, "_datadog_run_id_span_processor", None)
    if processor is not None:
        processor.unregister()
        delattr(mlflow, "_datadog_run_id_span_processor")

    # Empty global dict
    MLFLOW_ACTIVE_RUN_SPANS.clear()
    MLFLOW_ACTIVE_STEP_SPANS.clear()
    MLFLOW_CURRENT_STEPS.clear()
