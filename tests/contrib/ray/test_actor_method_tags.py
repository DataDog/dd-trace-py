"""Unit tests for Enrichment Task 9: actor-method span actor metadata tags.

These tests verify that ``RaySubmissionEvent`` and ``RayExecutionEvent`` correctly
carry the new actor identity fields and that the corresponding subscribers stamp
them as span tags — without requiring a live Ray cluster.
"""

from ddtrace import config
import ddtrace._trace.subscribers.ray  # noqa: F401 — registers Ray subscribers
from ddtrace._trace.subscribers.ray import RayExecutionSubscriber
from ddtrace._trace.subscribers.ray import RaySubmissionSubscriber
from ddtrace.contrib._events.ray import RayExecutionEvent
from ddtrace.contrib._events.ray import RaySubmissionEvent
from ddtrace.contrib.internal.ray.constants import RAY_ACTOR_CLASS_NAME
from ddtrace.contrib.internal.ray.constants import RAY_ACTOR_METHOD_NAME
from ddtrace.contrib.internal.ray.constants import RAY_ACTOR_MODULE_NAME
import ddtrace.contrib.internal.ray.patch  # noqa: F401 — triggers config._add("ray", ...) registration
from ddtrace.trace import tracer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_actor_submission_event(**extra):
    """Create a RaySubmissionEvent with sensible defaults for actor-method submission."""
    return RaySubmissionEvent(
        component=config.ray.integration_name,
        service="test-svc",
        resource="MyActor.train_step.remote",
        integration_config=config.ray,
        is_actor_method=True,
        is_task_submission=False,
        **extra,
    )


def _make_actor_execution_event(**extra):
    """Create a RayExecutionEvent with sensible defaults for actor-method execution."""
    return RayExecutionEvent(
        component=config.ray.integration_name,
        service="test-svc",
        resource="MyActor.train_step",
        integration_config=config.ray,
        is_actor_method=True,
        method_args=(),
        method_kwargs={},
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
# RaySubmissionEvent field population
# ---------------------------------------------------------------------------


def test_submission_event_carries_actor_metadata_fields():
    event = _make_actor_submission_event(
        actor_class_name="MyActor",
        actor_module_name="my.module",
        actor_method_name="train_step",
    )
    assert event.actor_class_name == "MyActor"
    assert event.actor_module_name == "my.module"
    assert event.actor_method_name == "train_step"


def test_submission_event_actor_metadata_defaults_to_none():
    event = _make_actor_submission_event()
    assert event.actor_class_name is None
    assert event.actor_module_name is None
    assert event.actor_method_name is None


# ---------------------------------------------------------------------------
# RayExecutionEvent field population
# ---------------------------------------------------------------------------


def test_execution_event_carries_actor_metadata_fields():
    event = _make_actor_execution_event(
        actor_class_name="MyActor",
        actor_module_name="my.module",
        actor_method_name="train_step",
    )
    assert event.actor_class_name == "MyActor"
    assert event.actor_module_name == "my.module"
    assert event.actor_method_name == "train_step"


def test_execution_event_actor_metadata_defaults_to_none():
    event = _make_actor_execution_event()
    assert event.actor_class_name is None
    assert event.actor_module_name is None
    assert event.actor_method_name is None


# ---------------------------------------------------------------------------
# RaySubmissionSubscriber stamps actor metadata tags on actor-method spans
# ---------------------------------------------------------------------------


def test_submission_span_carries_actor_metadata_tags():
    """Subscriber stamps actor identity tags on actor_method.submit spans."""
    event = _make_actor_submission_event(
        actor_class_name="MyActor",
        actor_module_name="my.module",
        actor_method_name="train_step",
    )

    ctx = _make_ctx_with_span(event)
    try:
        RaySubmissionSubscriber.on_started(ctx)
        span = ctx.span

        assert span.get_tag(RAY_ACTOR_CLASS_NAME) == "MyActor"
        assert span.get_tag(RAY_ACTOR_MODULE_NAME) == "my.module"
        assert span.get_tag(RAY_ACTOR_METHOD_NAME) == "train_step"
    finally:
        ctx.span.finish()


def test_submission_span_omits_none_actor_metadata_tags():
    """When actor metadata fields are None, no actor identity tags are stamped."""
    event = _make_actor_submission_event()  # all actor fields default to None

    ctx = _make_ctx_with_span(event)
    try:
        RaySubmissionSubscriber.on_started(ctx)
        span = ctx.span

        assert span.get_tag(RAY_ACTOR_CLASS_NAME) is None
        assert span.get_tag(RAY_ACTOR_MODULE_NAME) is None
        assert span.get_tag(RAY_ACTOR_METHOD_NAME) is None
    finally:
        ctx.span.finish()


def test_task_submission_does_not_get_actor_metadata_tags():
    """Task-submission spans must not receive actor identity tags."""
    event = RaySubmissionEvent(
        component=config.ray.integration_name,
        service="test-svc",
        resource="my_task.remote",
        integration_config=config.ray,
        is_task_submission=True,
        is_actor_method=False,
        # Even if these were somehow populated they should be ignored for tasks
        actor_class_name="ShouldNotAppear",
        actor_module_name="should.not.appear",
        actor_method_name="should_not_appear",
    )

    ctx = _make_ctx_with_span(event)
    try:
        RaySubmissionSubscriber.on_started(ctx)
        span = ctx.span

        # The subscriber must gate actor tags on is_actor_method
        assert span.get_tag(RAY_ACTOR_CLASS_NAME) is None
        assert span.get_tag(RAY_ACTOR_MODULE_NAME) is None
        assert span.get_tag(RAY_ACTOR_METHOD_NAME) is None
    finally:
        ctx.span.finish()


# ---------------------------------------------------------------------------
# RayExecutionSubscriber stamps actor metadata tags on actor-method spans
# ---------------------------------------------------------------------------


def test_execution_span_carries_actor_metadata_tags():
    """Subscriber stamps actor identity tags on actor_method.execute spans."""
    event = _make_actor_execution_event(
        actor_class_name="MyActor",
        actor_module_name="my.module",
        actor_method_name="train_step",
    )

    ctx = _make_ctx_with_span(event)
    try:
        RayExecutionSubscriber.on_started(ctx)
        span = ctx.span

        assert span.get_tag(RAY_ACTOR_CLASS_NAME) == "MyActor"
        assert span.get_tag(RAY_ACTOR_MODULE_NAME) == "my.module"
        assert span.get_tag(RAY_ACTOR_METHOD_NAME) == "train_step"
    finally:
        ctx.span.finish()


def test_execution_span_omits_none_actor_metadata_tags():
    """When actor metadata fields are None, no actor identity tags are stamped."""
    event = _make_actor_execution_event()  # all actor fields default to None

    ctx = _make_ctx_with_span(event)
    try:
        RayExecutionSubscriber.on_started(ctx)
        span = ctx.span

        assert span.get_tag(RAY_ACTOR_CLASS_NAME) is None
        assert span.get_tag(RAY_ACTOR_MODULE_NAME) is None
        assert span.get_tag(RAY_ACTOR_METHOD_NAME) is None
    finally:
        ctx.span.finish()


def test_remote_task_execution_does_not_get_actor_metadata_tags():
    """Remote-task execution spans must not receive actor identity tags."""
    event = RayExecutionEvent(
        component=config.ray.integration_name,
        service="test-svc",
        resource="my_task",
        integration_config=config.ray,
        is_actor_method=False,
        is_remote_task=True,
        method_args=(),
        method_kwargs={},
        # Even if these were somehow populated they should be ignored for tasks
        actor_class_name="ShouldNotAppear",
        actor_module_name="should.not.appear",
        actor_method_name="should_not_appear",
    )

    ctx = _make_ctx_with_span(event)
    try:
        RayExecutionSubscriber.on_started(ctx)
        span = ctx.span

        # The subscriber must gate actor tags on is_actor_method
        assert span.get_tag(RAY_ACTOR_CLASS_NAME) is None
        assert span.get_tag(RAY_ACTOR_MODULE_NAME) is None
        assert span.get_tag(RAY_ACTOR_METHOD_NAME) is None
    finally:
        ctx.span.finish()
