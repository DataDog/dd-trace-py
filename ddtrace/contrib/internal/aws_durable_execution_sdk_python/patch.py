"""
Instrumentation for the aws-durable-execution-sdk-python library.

Wraps three targets:
  - ``durable_execution`` decorator (workflow execution spans)
  - ``DurableContext.step`` (step execution spans)
  - ``DurableContext.invoke`` (cross-function invocation spans)

AIDEV-NOTE: SuspendExecution is a BaseException used for control flow.
It must NOT mark spans as errored. All wrappers explicitly handle it.
"""
import functools
import sys

import aws_durable_execution_sdk_python

from ddtrace import config
from ddtrace._trace.events import TracingEvent
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.propagation.http import HTTPPropagator as Propagator


log = get_logger(__name__)


config._add(
    "aws_durable_execution_sdk_python",
    dict(
        _default_service="aws_durable_execution_sdk_python",
        distributed_tracing_enabled=True,
    ),
)


def get_version() -> str:
    return getattr(aws_durable_execution_sdk_python, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"aws_durable_execution_sdk_python": ">=1.4.0"}


# ---------------------------------------------------------------------------
# AIDEV-NOTE: durable_execution is a decorator. Wrapping it intercepts the
# decoration call.  We replace the user function with a traced version so
# the execution span lives in the same thread as step/invoke spans, giving
# correct parent-child relationships without needing cross-thread context
# propagation.
# ---------------------------------------------------------------------------
def _traced_durable_execution(func, instance, args, kwargs):
    """Wrapper for the durable_execution decorator function.

    When ``@durable_execution`` is applied to a user function, this wrapper
    intercepts the call, replaces the user function with a traced version,
    and delegates to the original decorator.
    """
    # When called with keyword-only args (e.g. @durable_execution(boto3_client=...)),
    # func receives None and returns a functools.partial. Let it pass through;
    # the partial will call us again with the actual user function.
    user_func = args[0] if args else kwargs.get("func")

    if user_func is None or not callable(user_func):
        return func(*args, **kwargs)

    @functools.wraps(user_func)
    def traced_user_func(*inner_args, **inner_kwargs):
        """Traced wrapper around the user's workflow function."""
        # Lazy import to avoid errors when the SDK is not installed
        from aws_durable_execution_sdk_python.exceptions import SuspendExecution

        # AIDEV-NOTE: The user function signature is (input_event, durable_context).
        # We extract them safely from *args/**kwargs for metadata extraction,
        # keeping the wrapper resilient to future SDK signature changes.
        input_event = inner_args[0] if inner_args else inner_kwargs.get("input_event")
        durable_context = inner_args[1] if len(inner_args) > 1 else inner_kwargs.get("durable_context")

        # --- Extract metadata from the DurableContext ---
        tags = {}
        try:
            arn = getattr(durable_context.state, "durable_execution_arn", None) if durable_context else None
            if arn:
                tags["aws.durable_execution.arn"] = arn
        except Exception:
            pass
        try:
            if durable_context:
                status_enum = getattr(durable_context.state, "_replay_status", None)
                if status_enum is not None:
                    tags["aws.durable_execution.replay_status"] = status_enum.name  # "NEW" or "REPLAY"
        except Exception:
            pass

        # --- Extract distributed tracing context from input_event ---
        # AIDEV-NOTE: When a durable execution is invoked via DurableContext.invoke(),
        # the caller injects trace context into the payload dict under a "_datadog" key.
        # We extract it here to link this execution span to the caller's trace.
        distributed_context = None
        if config.aws_durable_execution_sdk_python.distributed_tracing_enabled:
            if isinstance(input_event, dict) and "_datadog" in input_event:
                try:
                    parent_ctx = Propagator.extract(input_event["_datadog"])
                    if parent_ctx.trace_id is not None:
                        distributed_context = parent_ctx
                except Exception:
                    pass

        # --- Create the execution span via event ---
        event = TracingEvent.create(
            component=config.aws_durable_execution_sdk_python.integration_name,
            integration_config=config.aws_durable_execution_sdk_python,
            operation_name="aws_durable_execution.execution",
            span_type="serverless",
            span_kind="server",
            resource="aws_durable_execution.execution",
            service=int_service(None, config.aws_durable_execution_sdk_python),
            tags=tags,
            distributed_context=distributed_context,
            use_active_context=distributed_context is None,
        )

        with core.context_with_event(event, dispatch_end_event=False) as ctx:
            try:
                result = user_func(*inner_args, **inner_kwargs)
            except SuspendExecution:
                # AIDEV-NOTE: SuspendExecution is control flow, not an error.
                ctx.dispatch_ended_event()
                raise
            except BaseException:
                ctx.dispatch_ended_event(*sys.exc_info())
                raise
            ctx.dispatch_ended_event()
            return result

    # Replace user func with traced version, call the original decorator
    new_args = (traced_user_func,) + args[1:]
    return func(*new_args, **kwargs)


def _try_tag_replayed(span, step_func, step_executed):
    """Tag a step span with whether it was replayed from checkpoint.

    AIDEV-NOTE: When the SDK replays a step from checkpoint, it returns the cached
    result without calling the step function.  The ``step_executed`` flag tracks
    whether the function was actually invoked, giving accurate per-step replay
    detection without relying on private SDK internals.
    """
    try:
        if callable(step_func):
            span.set_tag("aws.durable_execution.step.replayed", str(not step_executed[0]).lower())
    except Exception:
        pass


def _traced_step(func, instance, args, kwargs):
    """Wrapper for DurableContext.step().

    Creates a child span for each step execution with the step name
    and standard tags.
    """
    from aws_durable_execution_sdk_python.exceptions import SuspendExecution

    # --- Extract step name ---
    step_func = args[0] if args else None
    name_kwarg = kwargs.get("name")
    # Mirror the SDK's _resolve_step_name logic with __name__ fallback
    step_name = (
        name_kwarg
        or getattr(step_func, "_original_name", None)
        or getattr(step_func, "__name__", None)
    )

    # --- Wrap step function to detect replay vs fresh execution ---
    # AIDEV-NOTE: When the SDK replays a step from checkpoint, it returns the cached
    # result without calling the step function.  By wrapping the function and tracking
    # whether it was invoked, we accurately determine if the step was replayed without
    # relying on private SDK internals (e.g. operation_id or step_counter).
    _step_executed = [False]
    if callable(step_func):
        _original_step_func = step_func

        @functools.wraps(_original_step_func)
        def _tracked_step_func(*a, **kw):
            _step_executed[0] = True
            return _original_step_func(*a, **kw)

        args = (_tracked_step_func,) + args[1:]

    # --- Build tags ---
    tags = {}
    if step_name:
        tags["aws.durable_execution.step.name"] = step_name

    # --- Create the step span via event ---
    event = TracingEvent.create(
        component=config.aws_durable_execution_sdk_python.integration_name,
        integration_config=config.aws_durable_execution_sdk_python,
        operation_name="aws_durable_execution.step",
        span_type="worker",
        span_kind="internal",
        resource=step_name or "step",
        service=int_service(None, config.aws_durable_execution_sdk_python),
        tags=tags,
    )

    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            result = func(*args, **kwargs)
        except SuspendExecution:
            # AIDEV-NOTE: SuspendExecution is control flow, not an error.
            _try_tag_replayed(ctx.span, step_func, _step_executed)
            ctx.dispatch_ended_event()
            raise
        except BaseException:
            _try_tag_replayed(ctx.span, step_func, _step_executed)
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        _try_tag_replayed(ctx.span, step_func, _step_executed)
        ctx.dispatch_ended_event()
        return result


def _traced_invoke(func, instance, args, kwargs):
    """Wrapper for DurableContext.invoke().

    Creates a client span for cross-function invocations with the target
    function name and invocation name.
    """
    from aws_durable_execution_sdk_python.exceptions import SuspendExecution

    # --- Extract invoke parameters ---
    # invoke(function_name, payload, name=None, config=None)
    function_name = kwargs.get("function_name") or (args[0] if args else None)
    # payload is args[1] if positional
    invoke_name = kwargs.get("name")

    # --- Build tags ---
    tags = {}
    if function_name:
        tags["aws.durable_execution.invoke.function_name"] = str(function_name)
        # AIDEV-NOTE: out.host enables peer service computation by the
        # PeerServiceProcessor.  It derives peer.service from out.host on
        # client spans, giving downstream dependency visibility in APM.
        tags["out.host"] = str(function_name)
    if invoke_name:
        tags["aws.durable_execution.invoke.name"] = invoke_name

    # --- Create the invoke span via event ---
    event = TracingEvent.create(
        component=config.aws_durable_execution_sdk_python.integration_name,
        integration_config=config.aws_durable_execution_sdk_python,
        operation_name="aws_durable_execution.invoke",
        span_type="serverless",
        span_kind="client",
        resource=invoke_name or function_name or "invoke",
        service=int_service(None, config.aws_durable_execution_sdk_python),
        tags=tags,
    )

    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        # --- Inject distributed tracing context into payload ---
        # AIDEV-NOTE: When the payload is a dict, we inject trace context under a
        # "_datadog" key so the downstream @durable_execution handler can extract it
        # and link its execution span to this invoke span's trace.  We shallow-copy
        # the dict to avoid mutating the caller's original payload.
        if config.aws_durable_execution_sdk_python.distributed_tracing_enabled:
            try:
                payload = kwargs.get("payload") or (args[1] if len(args) > 1 else None)
                if isinstance(payload, dict):
                    _datadog_headers = {}
                    Propagator.inject(ctx.span.context, _datadog_headers)
                    payload = dict(payload)  # shallow copy to avoid mutating caller's dict
                    payload["_datadog"] = _datadog_headers
                    if "payload" in kwargs:
                        kwargs = dict(kwargs)
                        kwargs["payload"] = payload
                    elif len(args) > 1:
                        args = (args[0], payload) + args[2:]
            except Exception:
                pass

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


def patch():
    """Instrument the aws-durable-execution-sdk-python library."""
    if getattr(aws_durable_execution_sdk_python, "_datadog_patch", False):
        return

    aws_durable_execution_sdk_python._datadog_patch = True

    # AIDEV-NOTE: durable_execution is re-exported from the top-level __init__
    # via ``from .execution import durable_execution``. That creates a separate
    # name binding, so we must wrap in BOTH modules so that imports from either
    # path get the instrumented version.
    wrap("aws_durable_execution_sdk_python.execution", "durable_execution", _traced_durable_execution)
    wrap("aws_durable_execution_sdk_python", "durable_execution", _traced_durable_execution)

    wrap("aws_durable_execution_sdk_python.context", "DurableContext.step", _traced_step)
    wrap("aws_durable_execution_sdk_python.context", "DurableContext.invoke", _traced_invoke)


def unpatch():
    """Remove instrumentation from the aws-durable-execution-sdk-python library."""
    if not getattr(aws_durable_execution_sdk_python, "_datadog_patch", False):
        return

    aws_durable_execution_sdk_python._datadog_patch = False

    from aws_durable_execution_sdk_python import execution
    from aws_durable_execution_sdk_python.context import DurableContext

    unwrap(execution, "durable_execution")
    unwrap(aws_durable_execution_sdk_python, "durable_execution")
    unwrap(DurableContext, "step")
    unwrap(DurableContext, "invoke")
