from concurrent.futures import ThreadPoolExecutor
import contextvars
import functools
import sys
from typing import Any
from typing import Callable
from typing import NamedTuple

import aws_durable_execution_sdk_python
from aws_durable_execution_sdk_python import execution
import aws_durable_execution_sdk_python.concurrency.executor as executor_module
from aws_durable_execution_sdk_python.context import DurableContext
from aws_durable_execution_sdk_python.exceptions import SuspendExecution
from aws_durable_execution_sdk_python.lambda_service import OperationSubType
from aws_durable_execution_sdk_python.operation.base import OperationExecutor
from aws_durable_execution_sdk_python.operation.step import StepOperationExecutor

from ddtrace import config
from ddtrace import tracer
from ddtrace._trace.events import TracingEvent
from ddtrace.contrib.internal.aws_durable_execution_sdk_python.trace_checkpoint import (
    maybe_save_trace_context_checkpoint,
)
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value


log = get_logger(__name__)


config._add("aws_durable_execution_sdk_python", {})


_SPAN_NAME_PREFIX = "aws.durable."
_TAG_EXECUTION_ARN = "aws.durable.execution_arn"
_TAG_REPLAYED = "aws.durable.replayed"
_TAG_INVOCATION_STATUS = "aws.durable.invocation_status"
_TAG_INVOKE_FUNCTION_NAME = "aws.durable.invoke.function_name"


_SUSPEND_CAUSE_KEY = "aws_durable.suspend_cause"

# Span names whose direct children should not set a resource.
# Children of map/parallel can have unbounded names, increasing cardinality.
_DYNAMIC_PARENT_SPAN_NAMES = frozenset({"aws.durable.map", "aws.durable.parallel"})


def get_version() -> str:
    return getattr(aws_durable_execution_sdk_python, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"aws_durable_execution_sdk_python": ">=1.4.0"}


class TracedThreadPoolExecutor(ThreadPoolExecutor):
    """ThreadPoolExecutor that propagates trace context to worker threads.

    Captures context at init time as a fallback for re-submissions from
    background threads (e.g., SDK's TimerScheduler retries) that have no
    trace context.  At submit time, if the current thread has an active
    trace context it is used; otherwise the init-time context is used.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._init_ctx = contextvars.copy_context()

    def submit(self, fn, /, *args, **kwargs):
        active = tracer.context_provider.active()
        if active is not None:
            ctx = contextvars.copy_context()
        else:
            # Create a fresh copy — Context.run() is not reentrant so we
            # cannot share self._init_ctx across concurrent threads.
            ctx = self._init_ctx.run(contextvars.copy_context)

        def _wrapped(*a, **kw):
            return ctx.run(fn, *a, **kw)

        return super().submit(_wrapped, *args, **kwargs)


def _execution_tags(durable_context: DurableContext):
    """Read execution_arn and initial replay status from DurableContext."""
    tags = {}
    state = getattr(durable_context, "state", None)
    if state is not None:
        arn = getattr(state, "durable_execution_arn", None)
        if arn:
            tags[_TAG_EXECUTION_ARN] = arn
        status_enum = getattr(state, "_replay_status", None)
        if status_enum is not None:
            tags[_TAG_REPLAYED] = "true" if status_enum.name == "REPLAY" else "false"
    return tags


def _traced_durable_execution(wrapped: Callable, instance: Any, args: tuple, kwargs: dict):
    # Parameterized decorator @durable_execution(boto3_client=...) calls us with
    # us with no user function and returns a functools.partial, let it re-enter.
    user_func = get_argument_value(args, kwargs, 0, "func", optional=True)
    if user_func is None or not callable(user_func):
        return wrapped(*args, **kwargs)

    @functools.wraps(user_func)
    def traced_user_func(*inner_args, **inner_kwargs):
        durable_context = get_argument_value(inner_args, inner_kwargs, 1, "durable_context", optional=True)

        event = TracingEvent.create(
            component=config.aws_durable_execution_sdk_python.integration_name,
            integration_config=config.aws_durable_execution_sdk_python,
            operation_name="aws.durable.execute",
            span_type="serverless",
            span_kind="internal",
            resource="{}.{}".format(user_func.__module__, user_func.__qualname__),
            tags=_execution_tags(durable_context),
        )

        with core.context_with_event(event, dispatch_end_event=False) as ctx:
            try:
                result = user_func(*inner_args, **inner_kwargs)
            except SuspendExecution:
                # Workflow is pausing; another invocation will resume it.  This
                # is the only branch where it's worth persisting trace context
                # — the success and exception branches both end the workflow,
                # so a checkpoint there would never be read.
                ctx.span.set_tag(_TAG_INVOCATION_STATUS, "pending")
                if ctx.span is not None:
                    maybe_save_trace_context_checkpoint(durable_context, ctx.span)
                ctx.dispatch_ended_event()
                raise
            except BaseException:
                ctx.span.set_tag(_TAG_INVOCATION_STATUS, "failed")
                ctx.dispatch_ended_event(*sys.exc_info())
                raise
            ctx.span.set_tag(_TAG_INVOCATION_STATUS, "succeeded")
            ctx.dispatch_ended_event()
            return result

    return wrapped(traced_user_func, *args[1:], **kwargs)


def _is_top_level_for_span(operation_executor: OperationExecutor):
    """Skip executors that don't need to be tagged because they don't have corresponding spans.
    MapIteration and ParallelBranch are not traced as they are internal.
    """
    return getattr(operation_executor, "sub_type", None) not in (
        OperationSubType.MAP_ITERATION,
        OperationSubType.PARALLEL_BRANCH,
    )


def _traced_process(wrapped: Callable, instance: Any, args: tuple, kwargs: dict):
    """Set replayed tag based on checkpoint"""
    active = tracer.current_span()
    if active is not None and active.name.startswith(_SPAN_NAME_PREFIX) and _is_top_level_for_span(instance):
        checkpoint = instance.state.get_checkpoint_result(instance.operation_identifier.operation_id)
        active.set_tag(_TAG_REPLAYED, "true" if checkpoint.is_succeeded() else "false")
    return wrapped(*args, **kwargs)


def _traced_retry_handler(wrapped: Callable, instance: Any, args: tuple, kwargs: dict):
    """Store retried exception to be retrieved by _traced_context"""
    error = get_argument_value(args, kwargs, 0, "error", optional=True)
    if isinstance(error, Exception) and error.__traceback__ is not None:
        core.set_item(_SUSPEND_CAUSE_KEY, (type(error), error, error.__traceback__))
    return wrapped(*args, **kwargs)


class _ContextMethod(NamedTuple):
    method_name: str
    span_name: str
    name_pos: int


def _traced_context(method: _ContextMethod, wrapped: Callable, instance: Any, args: tuple, kwargs: dict):
    is_invoke = method.method_name == "invoke"
    tags = {}
    if is_invoke:
        tags[_TAG_INVOKE_FUNCTION_NAME] = get_argument_value(args, kwargs, 0, "function_name")

    parent = tracer.current_span()
    if parent is not None and parent.name in _DYNAMIC_PARENT_SPAN_NAMES:
        resource = None
    else:
        resource = get_argument_value(args, kwargs, method.name_pos, "name", optional=True)

    event = TracingEvent.create(
        component=config.aws_durable_execution_sdk_python.integration_name,
        integration_config=config.aws_durable_execution_sdk_python,
        operation_name=method.span_name,
        span_type="serverless",
        span_kind="client" if is_invoke else "internal",
        resource=resource,
        tags=tags,
    )

    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            result = wrapped(*args, **kwargs)
        except SuspendExecution:
            cause_exc_info = ctx.get_item(_SUSPEND_CAUSE_KEY)
            if cause_exc_info is not None:
                ctx.dispatch_ended_event(*cause_exc_info)
            else:
                ctx.dispatch_ended_event()
            raise
        except BaseException:
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        ctx.dispatch_ended_event()
        return result


_CONTEXT_METHODS: tuple[_ContextMethod, ...] = (
    _ContextMethod("invoke", "aws.durable.invoke", 2),
    _ContextMethod("step", "aws.durable.step", 1),
    _ContextMethod("wait", "aws.durable.wait", 1),
    _ContextMethod("wait_for_condition", "aws.durable.wait_for_condition", 2),
    _ContextMethod("wait_for_callback", "aws.durable.wait_for_callback", 1),
    _ContextMethod("create_callback", "aws.durable.create_callback", 0),
    _ContextMethod("map", "aws.durable.map", 2),
    _ContextMethod("parallel", "aws.durable.parallel", 1),
    _ContextMethod("run_in_child_context", "aws.durable.child_context", 1),
)


def patch():
    """Instrument the aws-durable-execution-sdk-python library."""
    if getattr(aws_durable_execution_sdk_python, "_datadog_patch", False):
        return

    aws_durable_execution_sdk_python._datadog_patch = True

    # AIDEV-NOTE: durable_execution is re-exported from the top-level __init__
    # via ``from .execution import durable_execution``. That creates a separate
    # name binding, so we must wrap in BOTH modules.
    wrap("aws_durable_execution_sdk_python.execution", "durable_execution", _traced_durable_execution)
    wrap("aws_durable_execution_sdk_python", "durable_execution", _traced_durable_execution)

    for method in _CONTEXT_METHODS:
        wrap(
            "aws_durable_execution_sdk_python.context",
            f"DurableContext.{method.method_name}",
            functools.partial(_traced_context, method),
        )

    executor_module.ThreadPoolExecutor = TracedThreadPoolExecutor

    wrap("aws_durable_execution_sdk_python.operation.base", "OperationExecutor.process", _traced_process)
    wrap(
        "aws_durable_execution_sdk_python.operation.step", "StepOperationExecutor.retry_handler", _traced_retry_handler
    )


def unpatch():
    """Remove instrumentation from the aws-durable-execution-sdk-python library."""
    if not getattr(aws_durable_execution_sdk_python, "_datadog_patch", False):
        return

    aws_durable_execution_sdk_python._datadog_patch = False

    unwrap(execution, "durable_execution")
    unwrap(aws_durable_execution_sdk_python, "durable_execution")

    for method in _CONTEXT_METHODS:
        unwrap(DurableContext, method.method_name)

    executor_module.ThreadPoolExecutor = ThreadPoolExecutor

    unwrap(OperationExecutor, "process")
    unwrap(StepOperationExecutor, "retry_handler")
