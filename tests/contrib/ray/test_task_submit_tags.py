"""Tests for task scheduling-hint tags on ray.task.submit spans."""

from ddtrace import config
import ddtrace._trace.subscribers.ray  # noqa: F401 — registers RaySubmissionSubscriber
from ddtrace._trace.subscribers.ray import RaySubmissionSubscriber
from ddtrace.contrib._events.ray import RaySubmissionEvent
from ddtrace.contrib.internal.ray.constants import RAY_TASK_ACCELERATOR_TYPE
from ddtrace.contrib.internal.ray.constants import RAY_TASK_FUNCTION_MODULE
from ddtrace.contrib.internal.ray.constants import RAY_TASK_FUNCTION_QUALNAME
from ddtrace.contrib.internal.ray.constants import RAY_TASK_MAX_RETRIES
from ddtrace.contrib.internal.ray.constants import RAY_TASK_NUM_CPUS
from ddtrace.contrib.internal.ray.constants import RAY_TASK_NUM_GPUS
from ddtrace.contrib.internal.ray.constants import RAY_TASK_NUM_RETURNS
from ddtrace.contrib.internal.ray.constants import RAY_TASK_RESOURCES_PREFIX
from ddtrace.contrib.internal.ray.constants import RAY_TASK_SCHEDULING_STRATEGY
from ddtrace.contrib.internal.ray.core.remote_function import _as_dict
from ddtrace.contrib.internal.ray.core.remote_function import _as_float
from ddtrace.contrib.internal.ray.core.remote_function import _as_int
from ddtrace.contrib.internal.ray.core.remote_function import _as_str
import ddtrace.contrib.internal.ray.patch  # noqa: F401 — triggers config._add("ray", ...) registration
from ddtrace.trace import tracer


# ---------------------------------------------------------------------------
# Helper: build a minimal RaySubmissionEvent for task submission.
# ---------------------------------------------------------------------------


def _make_submission_event(**extra):
    """Create a RaySubmissionEvent with sensible defaults for task submission."""
    return RaySubmissionEvent(
        component=config.ray.integration_name,
        service="test-svc",
        resource="my_task.remote",
        integration_config=config.ray,
        is_task_submission=True,
        **extra,
    )


def _make_ctx_with_span(event):
    """Create a minimal mock context holding the event and a live span."""

    class _Ctx:
        pass

    ctx = _Ctx()
    ctx.event = event
    ctx.span = tracer.start_span(event.operation_name, service=event.service, resource=event.resource)
    return ctx


# ---------------------------------------------------------------------------
# Helper conversions
# ---------------------------------------------------------------------------


def test_as_float_converts_numeric():
    assert _as_float(2) == 2.0
    assert _as_float(1.5) == 1.5
    assert _as_float("3") == 3.0


def test_as_float_returns_none_for_none():
    assert _as_float(None) is None


def test_as_float_returns_none_for_non_numeric():
    assert _as_float("not-a-number") is None
    assert _as_float(object()) is None


def test_as_int_converts_numeric():
    assert _as_int(3) == 3
    assert _as_int("4") == 4
    assert _as_int(2.9) == 2


def test_as_int_returns_none_for_none():
    assert _as_int(None) is None


def test_as_int_returns_none_for_non_numeric():
    assert _as_int("abc") is None


def test_as_str_converts_values():
    assert _as_str(42) == "42"
    assert _as_str("hello") == "hello"


def test_as_str_returns_none_for_none():
    assert _as_str(None) is None


def test_as_dict_accepts_dict():
    d = {"GPU": 2.0}
    assert _as_dict(d) is d


def test_as_dict_rejects_non_dict():
    assert _as_dict([1, 2]) is None
    assert _as_dict("str") is None
    assert _as_dict(None) is None


# ---------------------------------------------------------------------------
# RaySubmissionEvent field population
# ---------------------------------------------------------------------------


def test_submission_event_carries_scheduling_fields():
    event = _make_submission_event(
        task_num_cpus=2.0,
        task_num_gpus=1.0,
        task_num_returns=3,
        task_max_retries=5,
        task_resources={"custom_resource": 1.0},
        task_scheduling_strategy="NodeAffinitySchedulingStrategy",
        task_accelerator_type="V100",
        task_function_module="mymodule",
        task_function_qualname="MyClass.my_task",
    )
    assert event.task_num_cpus == 2.0
    assert event.task_num_gpus == 1.0
    assert event.task_num_returns == 3
    assert event.task_max_retries == 5
    assert event.task_resources == {"custom_resource": 1.0}
    assert event.task_scheduling_strategy == "NodeAffinitySchedulingStrategy"
    assert event.task_accelerator_type == "V100"
    assert event.task_function_module == "mymodule"
    assert event.task_function_qualname == "MyClass.my_task"


def test_submission_event_defaults_to_none():
    event = _make_submission_event()
    assert event.task_num_cpus is None
    assert event.task_num_gpus is None
    assert event.task_num_returns is None
    assert event.task_max_retries is None
    assert event.task_resources is None
    assert event.task_scheduling_strategy is None
    assert event.task_accelerator_type is None
    assert event.task_function_module is None
    assert event.task_function_qualname is None


# ---------------------------------------------------------------------------
# Subscriber integration: span tags are stamped by RaySubmissionSubscriber.on_started
# ---------------------------------------------------------------------------


def test_submission_span_carries_scheduling_tags():
    """Subscriber stamps all scheduling-hint tags on a task.submit span."""
    event = _make_submission_event(
        task_num_cpus=4.0,
        task_num_gpus=2.0,
        task_num_returns=1,
        task_max_retries=3,
        task_resources={"my_custom_gpu": 1.0},
        task_scheduling_strategy="PlacementGroupSchedulingStrategy",
        task_accelerator_type="A100",
        task_function_module="mypackage.tasks",
        task_function_qualname="compute_task",
    )

    ctx = _make_ctx_with_span(event)
    try:
        RaySubmissionSubscriber.on_started(ctx)
        span = ctx.span

        assert span.get_metric(RAY_TASK_NUM_CPUS) == 4.0
        assert span.get_metric(RAY_TASK_NUM_GPUS) == 2.0
        assert span.get_metric(RAY_TASK_NUM_RETURNS) == 1
        assert span.get_metric(RAY_TASK_MAX_RETRIES) == 3
        assert span.get_tag(RAY_TASK_ACCELERATOR_TYPE) == "A100"
        assert span.get_tag(RAY_TASK_SCHEDULING_STRATEGY) == "PlacementGroupSchedulingStrategy"
        assert span.get_tag(RAY_TASK_FUNCTION_MODULE) == "mypackage.tasks"
        assert span.get_tag(RAY_TASK_FUNCTION_QUALNAME) == "compute_task"
        # Resource tag: ray.task.resources.my_custom_gpu
        assert span.get_metric(f"{RAY_TASK_RESOURCES_PREFIX}my_custom_gpu") == 1.0
    finally:
        ctx.span.finish()


def test_submission_span_omits_none_scheduling_tags():
    """When optional fields are None, no extra tags are stamped."""
    event = _make_submission_event()  # all scheduling fields default to None

    ctx = _make_ctx_with_span(event)
    try:
        RaySubmissionSubscriber.on_started(ctx)
        span = ctx.span

        assert span.get_metric(RAY_TASK_NUM_CPUS) is None
        assert span.get_metric(RAY_TASK_NUM_GPUS) is None
        assert span.get_metric(RAY_TASK_NUM_RETURNS) is None
        assert span.get_metric(RAY_TASK_MAX_RETRIES) is None
        assert span.get_tag(RAY_TASK_ACCELERATOR_TYPE) is None
        assert span.get_tag(RAY_TASK_SCHEDULING_STRATEGY) is None
        assert span.get_tag(RAY_TASK_FUNCTION_MODULE) is None
        assert span.get_tag(RAY_TASK_FUNCTION_QUALNAME) is None
    finally:
        ctx.span.finish()


def test_actor_method_submission_does_not_get_task_tags():
    """Actor-method submissions must not receive task scheduling tags."""
    event = RaySubmissionEvent(
        component=config.ray.integration_name,
        service="test-svc",
        resource="MyActor.method.remote",
        integration_config=config.ray,
        is_task_submission=False,
        is_actor_method=True,
        # Even if these were somehow populated they should be ignored for actors
        task_num_cpus=8.0,
        task_num_gpus=4.0,
    )

    ctx = _make_ctx_with_span(event)
    try:
        RaySubmissionSubscriber.on_started(ctx)
        span = ctx.span

        # The subscriber must gate task tags on is_task_submission
        assert span.get_metric(RAY_TASK_NUM_CPUS) is None
        assert span.get_metric(RAY_TASK_NUM_GPUS) is None
    finally:
        ctx.span.finish()


def test_submission_span_resources_multiple_keys():
    """Multiple resource keys each become a separate span attribute."""
    event = _make_submission_event(
        task_resources={"accelerator_v100": 2.0, "custom_memory": 4.0},
    )

    ctx = _make_ctx_with_span(event)
    try:
        RaySubmissionSubscriber.on_started(ctx)
        span = ctx.span

        assert span.get_metric(f"{RAY_TASK_RESOURCES_PREFIX}accelerator_v100") == 2.0
        assert span.get_metric(f"{RAY_TASK_RESOURCES_PREFIX}custom_memory") == 4.0
    finally:
        ctx.span.finish()
