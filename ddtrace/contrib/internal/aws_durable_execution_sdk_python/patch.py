from concurrent.futures import ThreadPoolExecutor
import contextvars
import functools
import sys
from typing import NamedTuple

import aws_durable_execution_sdk_python
from aws_durable_execution_sdk_python import execution
import aws_durable_execution_sdk_python.concurrency.executor as executor_module
from aws_durable_execution_sdk_python.context import DurableContext
from aws_durable_execution_sdk_python.exceptions import SuspendExecution
from aws_durable_execution_sdk_python.lambda_service import OperationSubType
from aws_durable_execution_sdk_python.operation.base import OperationExecutor

from ddtrace import config
from ddtrace import tracer
from ddtrace._trace.events import TracingEvent
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value


log = get_logger(__name__)


config._add("aws_durable_execution_sdk_python", {})


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


def _parse_durable_execution_arn(arn):
    """Extract execution_name and execution_id from a durable execution ARN.

    Expected format: arn:aws:lambda:{region}:{account}:function:{name}:dex:{id}
    """
    parts = arn.split(":")
    if len(parts) >= 9 and parts[7] == "dex":
        return parts[6], parts[8]
    return None, None


def _execution_tags(durable_context):
    """Read execution_arn and initial replay status off the SDK's DurableContext."""
    tags = {}
    state = getattr(durable_context, "state", None)
    if state is not None:
        arn = getattr(state, "durable_execution_arn", None)
        if arn:
            tags["aws.durable.execution_arn"] = arn
            execution_name, execution_id = _parse_durable_execution_arn(arn)
            if execution_name:
                tags["aws_lambda.durable_execution.execution_name"] = execution_name
            if execution_id:
                tags["aws_lambda.durable_execution.execution_id"] = execution_id
        status_enum = getattr(state, "_replay_status", None)
        if status_enum is not None:
            tags["aws.durable.replayed"] = "true" if status_enum.name == "REPLAY" else "false"
    return tags


def _traced_durable_execution(func, instance, args, kwargs):
    # Keyword-only invocation (e.g. @durable_execution(boto3_client=...)) calls
    # us with func=None and returns a functools.partial; let that partial re-enter.
    user_func = get_argument_value(args, kwargs, 0, "func", optional=True)
    if user_func is None or not callable(user_func):
        return func(*args, **kwargs)

    @functools.wraps(user_func)
    def traced_user_func(*inner_args, **inner_kwargs):
        durable_context = get_argument_value(inner_args, inner_kwargs, 1, "durable_context", optional=True)

        event = TracingEvent.create(
            component=config.aws_durable_execution_sdk_python.integration_name,
            integration_config=config.aws_durable_execution_sdk_python,
            operation_name="aws.durable.execute",
            span_type="serverless",
            span_kind="server",
            resource="{}.{}".format(user_func.__module__, user_func.__qualname__),
            tags=_execution_tags(durable_context),
        )

        with core.context_with_event(event, dispatch_end_event=False) as ctx:
            try:
                result = user_func(*inner_args, **inner_kwargs)
            except SuspendExecution:
                # SuspendExecution is control flow (InvocationStatus.PENDING), not an error.
                ctx.span.set_tag("aws.durable.invocation_status", "pending")
                ctx.dispatch_ended_event()
                raise
            except BaseException:
                ctx.span.set_tag("aws.durable.invocation_status", "failed")
                ctx.dispatch_ended_event(*sys.exc_info())
                raise
            ctx.span.set_tag("aws.durable.invocation_status", "succeeded")
            ctx.dispatch_ended_event()
            return result

    return func(traced_user_func, *args[1:], **kwargs)


def _is_top_level_for_span(operation_executor):
    """Skip executors that don't need to be tagged because they don't have corresponding spans.
    MapIteration and ParallelBranch are not traced as they are internal.
    """
    return getattr(operation_executor, "sub_type", None) not in (
        OperationSubType.MAP_ITERATION,
        OperationSubType.PARALLEL_BRANCH,
    )


def _traced_process(wrapped, instance, args, kwargs):
    active = tracer.current_span()
    if active is not None and active.name.startswith("aws.durable.") and _is_top_level_for_span(instance):
        checkpoint = instance.state.get_checkpoint_result(instance.operation_identifier.operation_id)
        active.set_tag("aws.durable.replayed", "true" if checkpoint.is_succeeded() else "false")
    return wrapped(*args, **kwargs)


class _ContextMethod(NamedTuple):
    method_name: str
    span_name: str
    name_pos: int


def _traced_context(method: _ContextMethod, func, instance, args, kwargs):
    tags = {}
    if method.method_name == "invoke":
        tags["aws.durable.invoke.function_name"] = get_argument_value(args, kwargs, 0, "function_name")

    event = TracingEvent.create(
        component=config.aws_durable_execution_sdk_python.integration_name,
        integration_config=config.aws_durable_execution_sdk_python,
        operation_name=method.span_name,
        span_type="worker",
        span_kind="internal",
        resource=get_argument_value(args, kwargs, method.name_pos, "name", optional=True),
        tags=tags,
    )

    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            result = func(*args, **kwargs)
        except SuspendExecution:
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
