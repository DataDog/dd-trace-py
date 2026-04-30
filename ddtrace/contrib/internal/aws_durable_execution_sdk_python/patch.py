from concurrent.futures import ThreadPoolExecutor
import contextvars
import functools
from typing import Any
from typing import Callable

import aws_durable_execution_sdk_python
from aws_durable_execution_sdk_python import execution
import aws_durable_execution_sdk_python.concurrency.executor as executor_module
from aws_durable_execution_sdk_python.context import DurableContext
from aws_durable_execution_sdk_python.exceptions import SuspendExecution
from aws_durable_execution_sdk_python.lambda_service import OperationSubType
from aws_durable_execution_sdk_python.operation.base import OperationExecutor
from aws_durable_execution_sdk_python.operation.step import StepOperationExecutor

from ddtrace.contrib.internal.aws_durable_execution_sdk_python.trace_checkpoint import (
    maybe_save_trace_context_checkpoint,
)
from ddtrace.contrib._events.aws_durable import AwsDurableExecuteEvent
from ddtrace.contrib._events.aws_durable import AwsDurableInvokeEvent
from ddtrace.contrib._events.aws_durable import AwsDurableOperationEvent
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings._config import config
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.trace import tracer


log = get_logger(__name__)


config._add("aws_durable_execution_sdk_python", {})


# Operations whose direct children should not set a resource.
# Children of map/parallel can have unbounded names, increasing cardinality.
_DYNAMIC_PARENT_OPERATIONS = frozenset({"aws.durable.map", "aws.durable.parallel"})


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


def _read_execution_state(durable_context):
    """Return (execution_arn, is_replay_execution) from a DurableContext."""
    state = getattr(durable_context, "state", None)
    if state is None:
        return None, None
    arn = getattr(state, "durable_execution_arn", None) or None
    status_enum = getattr(state, "_replay_status", None)
    is_replay = (status_enum.name == "REPLAY") if status_enum is not None else None
    return arn, is_replay


def _traced_durable_execution(wrapped: Callable, instance: Any, args: tuple, kwargs: dict):
    # Parameterized decorator @durable_execution(boto3_client=...) calls us with
    # us with no user function and returns a functools.partial, let it re-enter.
    user_func = get_argument_value(args, kwargs, 0, "func", optional=True)
    if user_func is None or not callable(user_func):
        return wrapped(*args, **kwargs)

    qualname = (
        getattr(user_func, "__qualname__", None) or getattr(user_func, "__name__", None) or type(user_func).__name__
    )
    module = getattr(user_func, "__module__", None)
    resource = f"{module}.{qualname}" if module else qualname

    @functools.wraps(user_func)
    def traced_user_func(*inner_args, **inner_kwargs):
        durable_context = get_argument_value(inner_args, inner_kwargs, 1, "durable_context", optional=True)
        arn, is_replay = _read_execution_state(durable_context)

        event = AwsDurableExecuteEvent(
            component=config.aws_durable_execution_sdk_python.integration_name,
            integration_config=config.aws_durable_execution_sdk_python,
            resource=resource,
            execution_arn=arn,
            is_replay_execution=is_replay,
        )

        with core.context_with_event(event) as ctx:
            try:
                return user_func(*inner_args, **inner_kwargs)
            except SuspendExecution:
                # Dispatch without exc_info so __exit__ skips auto-dispatch
                # and the span is not tagged with the exception.
                ctx.event.suspended = True
                # Workflow is pausing; another invocation will resume it. This
                # is the only branch where it's worth persisting trace context.
                if ctx.span is not None:
                    maybe_save_trace_context_checkpoint(durable_context, ctx.span)
                ctx.dispatch_ended_event()
                raise

    new_args, new_kwargs = set_argument_value(args, kwargs, 0, "func", traced_user_func)
    return wrapped(*new_args, **new_kwargs)


def _is_top_level_for_span(operation_executor: OperationExecutor):
    """Skip executors that don't need to be tagged because they don't have corresponding spans.
    MapIteration and ParallelBranch are not traced as they are internal.
    """
    return getattr(operation_executor, "sub_type", None) not in (
        OperationSubType.MAP_ITERATION,
        OperationSubType.PARALLEL_BRANCH,
    )


def _traced_process(wrapped: Callable, instance: Any, args: tuple, kwargs: dict):
    """Record checkpoint replay status on the active operation event."""
    if _is_top_level_for_span(instance):
        event = core.current.event
        if isinstance(event, (AwsDurableInvokeEvent, AwsDurableOperationEvent)):
            operation_id = instance.operation_identifier.operation_id
            checkpoint = instance.state.get_checkpoint_result(operation_id)
            event.replayed = checkpoint.is_succeeded()
            event.id = operation_id
    return wrapped(*args, **kwargs)


def _traced_retry_handler(wrapped: Callable, instance: Any, args: tuple, kwargs: dict):
    """Store retry cause on the active operation event for later use by _trace_with_event."""
    error = get_argument_value(args, kwargs, 0, "error", optional=True)
    if isinstance(error, Exception) and error.__traceback__ is not None:
        event = core.current.event
        if isinstance(event, (AwsDurableInvokeEvent, AwsDurableOperationEvent)):
            event.suspend_cause_exc_info = (type(error), error, error.__traceback__)
    return wrapped(*args, **kwargs)


def _is_dynamic_parent() -> bool:
    """Return True if the active parent event is a map/parallel operation."""
    parent_event = core.current.event
    return isinstance(parent_event, AwsDurableOperationEvent) and parent_event.operation in _DYNAMIC_PARENT_OPERATIONS


def _trace_with_event(event, wrapped: Callable, args: tuple, kwargs: dict):
    """Run ``wrapped`` inside a context with ``event``, attaching the cause on suspension."""
    with core.context_with_event(event) as ctx:
        try:
            return wrapped(*args, **kwargs)
        except SuspendExecution:
            # Dispatch without exc_info so __exit__ skips auto-dispatch
            # and the span is not tagged with the exception.
            cause_exc_info = ctx.event.suspend_cause_exc_info
            if cause_exc_info is not None:
                ctx.dispatch_ended_event(*cause_exc_info)
            else:
                ctx.dispatch_ended_event()
            raise


def _traced_invoke(wrapped: Callable, instance: Any, args: tuple, kwargs: dict):
    name = get_argument_value(args, kwargs, 2, "name", optional=True)
    event = AwsDurableInvokeEvent(
        component=config.aws_durable_execution_sdk_python.integration_name,
        integration_config=config.aws_durable_execution_sdk_python,
        resource=None if _is_dynamic_parent() else name,
        invoke_function_name=get_argument_value(args, kwargs, 0, "function_name"),
        name=name,
    )
    return _trace_with_event(event, wrapped, args, kwargs)


def _traced_operation(operation: str, name_pos: int) -> Callable:
    """Build a wrapper that traces a DurableContext operation method (step/wait/map/parallel/etc.)."""

    def wrapper(wrapped: Callable, instance: Any, args: tuple, kwargs: dict):
        name = get_argument_value(args, kwargs, name_pos, "name", optional=True)
        event = AwsDurableOperationEvent(
            component=config.aws_durable_execution_sdk_python.integration_name,
            integration_config=config.aws_durable_execution_sdk_python,
            resource=None if _is_dynamic_parent() else name,
            operation=operation,
            name=name,
        )
        return _trace_with_event(event, wrapped, args, kwargs)

    return wrapper


def patch():
    """Instrument the aws-durable-execution-sdk-python library."""
    if getattr(aws_durable_execution_sdk_python, "_datadog_patch", False):
        return

    import ddtrace._trace.subscribers.aws_durable  # noqa: F401

    aws_durable_execution_sdk_python._datadog_patch = True

    # AIDEV-NOTE: durable_execution is re-exported from the top-level __init__
    # via ``from .execution import durable_execution``. That creates a separate
    # name binding, so we must wrap in BOTH modules.
    wrap("aws_durable_execution_sdk_python.execution", "durable_execution", _traced_durable_execution)
    wrap("aws_durable_execution_sdk_python", "durable_execution", _traced_durable_execution)

    wrap("aws_durable_execution_sdk_python.context", "DurableContext.invoke", _traced_invoke)
    wrap("aws_durable_execution_sdk_python.context", "DurableContext.step", _traced_operation("aws.durable.step", 1))
    wrap("aws_durable_execution_sdk_python.context", "DurableContext.wait", _traced_operation("aws.durable.wait", 1))
    wrap(
        "aws_durable_execution_sdk_python.context",
        "DurableContext.wait_for_condition",
        _traced_operation("aws.durable.wait_for_condition", 2),
    )
    wrap(
        "aws_durable_execution_sdk_python.context",
        "DurableContext.wait_for_callback",
        _traced_operation("aws.durable.wait_for_callback", 1),
    )
    wrap(
        "aws_durable_execution_sdk_python.context",
        "DurableContext.create_callback",
        _traced_operation("aws.durable.create_callback", 0),
    )
    wrap("aws_durable_execution_sdk_python.context", "DurableContext.map", _traced_operation("aws.durable.map", 2))
    wrap(
        "aws_durable_execution_sdk_python.context",
        "DurableContext.parallel",
        _traced_operation("aws.durable.parallel", 1),
    )
    wrap(
        "aws_durable_execution_sdk_python.context",
        "DurableContext.run_in_child_context",
        _traced_operation("aws.durable.child_context", 1),
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

    unwrap(DurableContext, "invoke")
    unwrap(DurableContext, "step")
    unwrap(DurableContext, "wait")
    unwrap(DurableContext, "wait_for_condition")
    unwrap(DurableContext, "wait_for_callback")
    unwrap(DurableContext, "create_callback")
    unwrap(DurableContext, "map")
    unwrap(DurableContext, "parallel")
    unwrap(DurableContext, "run_in_child_context")

    executor_module.ThreadPoolExecutor = ThreadPoolExecutor

    unwrap(OperationExecutor, "process")
    unwrap(StepOperationExecutor, "retry_handler")
