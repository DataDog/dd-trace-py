"""
Instrumentation for the aws-durable-execution-sdk-python library.

Wraps the following targets:
  - ``durable_execution`` decorator (workflow execution spans)
  - ``DurableContext.step`` (step execution spans)
  - ``DurableContext.invoke`` (cross-function invocation spans)
  - ``DurableContext.wait`` (wait spans)
  - ``DurableContext.wait_for_condition`` (wait-for-condition spans)
  - ``DurableContext.wait_for_callback`` (wait-for-callback spans)
  - ``DurableContext.create_callback`` (create-callback spans)
  - ``DurableContext.map`` (map spans)
  - ``DurableContext.parallel`` (parallel spans)
  - ``DurableContext.run_in_child_context`` (child-context spans)

Also patches the SDK's internal ThreadPoolExecutor to propagate trace
context into worker threads used by map/parallel operations.

AIDEV-NOTE: SuspendExecution is a BaseException used for control flow.
It must NOT mark spans as errored. All wrappers explicitly handle it.
"""
import contextvars
import functools
import os
import sys
from concurrent.futures import ThreadPoolExecutor

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
        _default_service="aws.durable_functions",
        distributed_tracing_enabled=True,
    ),
)


def get_version() -> str:
    return getattr(aws_durable_execution_sdk_python, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"aws_durable_execution_sdk_python": ">=1.4.0"}


# ---------------------------------------------------------------------------
# Service-name helper
# ---------------------------------------------------------------------------

def _get_service():
    """Return the service name for all durable-function spans.

    Checks ``DD_DURABLE_EXECUTION_SERVICE`` first (short alias shared with
    the Node.js integration), then falls back to the standard dd-trace-py
    resolution chain (``DD_AWS_DURABLE_EXECUTION_SDK_PYTHON_SERVICE`` →
    ``DD_SERVICE`` → config default).
    """
    custom = os.environ.get("DD_DURABLE_EXECUTION_SERVICE")
    if custom:
        return custom
    return int_service(None, config.aws_durable_execution_sdk_python)


# ---------------------------------------------------------------------------
# TracedThreadPoolExecutor — trace-context propagation for map/parallel
# ---------------------------------------------------------------------------

_OriginalThreadPoolExecutor = ThreadPoolExecutor


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
        from ddtrace import tracer

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


# ---------------------------------------------------------------------------
# Wrapper: durable_execution decorator
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
        from aws_durable_execution_sdk_python.exceptions import SuspendExecution

        # AIDEV-NOTE: The user function signature is (input_event, durable_context).
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
                    tags["aws.durable_execution.replay_status"] = status_enum.name
        except Exception:
            pass

        # --- Extract distributed tracing context from input_event ---
        # AIDEV-NOTE: When a durable execution is invoked via DurableContext.invoke(),
        # the caller injects trace context into the payload dict under a "_datadog" key.
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
            operation_name="aws.durable_functions.execute",
            span_type="serverless",
            span_kind="server",
            resource="aws.durable_functions.execute",
            service=_get_service(),
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


# ---------------------------------------------------------------------------
# Wrapper: DurableContext.step
# ---------------------------------------------------------------------------

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
    step_name = (
        name_kwarg
        or getattr(step_func, "_original_name", None)
        or getattr(step_func, "__name__", None)
    )

    # --- Wrap step function to detect replay vs fresh execution ---
    # AIDEV-NOTE: When the SDK replays a step from checkpoint, it returns the cached
    # result without calling the step function.  By wrapping the function and tracking
    # whether it was invoked, we accurately determine if the step was replayed.
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
        operation_name="aws.durable_functions.step",
        span_type="worker",
        span_kind="internal",
        resource=step_name or "step",
        service=_get_service(),
        tags=tags,
    )

    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            result = func(*args, **kwargs)
        except SuspendExecution:
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


# ---------------------------------------------------------------------------
# Wrapper: DurableContext.invoke
# ---------------------------------------------------------------------------

def _traced_invoke(func, instance, args, kwargs):
    """Wrapper for DurableContext.invoke().

    Creates a client span for cross-function invocations with the target
    function name and invocation name.
    """
    from aws_durable_execution_sdk_python.exceptions import SuspendExecution

    # --- Extract invoke parameters ---
    # invoke(function_name, payload, name=None, config=None)
    function_name = kwargs.get("function_name") or (args[0] if args else None)
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
        operation_name="aws.durable_functions.invoke",
        span_type="serverless",
        span_kind="client",
        resource=invoke_name or function_name or "invoke",
        service=_get_service(),
        tags=tags,
    )

    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        # --- Inject distributed tracing context into payload ---
        # AIDEV-NOTE: When the payload is a dict, we inject trace context under a
        # "_datadog" key so the downstream @durable_execution handler can extract it
        # and link its execution span to this invoke span's trace.
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


# ---------------------------------------------------------------------------
# Factory for simple internal operations (wait, callback, map, etc.)
# ---------------------------------------------------------------------------

def _make_traced_internal(span_name, default_resource):
    """Create a wrapper for an internal durable operation.

    All internal operations follow the same tracing pattern: extract an
    optional ``name`` kwarg for the resource, create an internal span,
    handle SuspendExecution as non-error control flow, and propagate
    real exceptions with error tags.
    """
    def _traced(func, instance, args, kwargs):
        from aws_durable_execution_sdk_python.exceptions import SuspendExecution

        name = kwargs.get("name")

        event = TracingEvent.create(
            component=config.aws_durable_execution_sdk_python.integration_name,
            integration_config=config.aws_durable_execution_sdk_python,
            operation_name=span_name,
            span_type="worker",
            span_kind="internal",
            resource=name or default_resource,
            service=_get_service(),
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

    return _traced


_traced_wait = _make_traced_internal("aws.durable_functions.wait", "wait")
_traced_wait_for_condition = _make_traced_internal("aws.durable_functions.wait_for_condition", "wait_for_condition")
_traced_wait_for_callback = _make_traced_internal("aws.durable_functions.wait_for_callback", "wait_for_callback")
_traced_create_callback = _make_traced_internal("aws.durable_functions.create_callback", "create_callback")
_traced_map = _make_traced_internal("aws.durable_functions.map", "map")
_traced_parallel = _make_traced_internal("aws.durable_functions.parallel", "parallel")
_traced_run_in_child_context = _make_traced_internal("aws.durable_functions.child_context", "child_context")


# ---------------------------------------------------------------------------
# patch / unpatch
# ---------------------------------------------------------------------------

def _try_wrap(module_path, target, wrapper, label):
    """Wrap *target* in *module_path*, logging gracefully on failure."""
    try:
        wrap(module_path, target, wrapper)
    except (ImportError, AttributeError):
        log.debug("Could not wrap %s (may not exist in this SDK version)", label)


def patch():
    """Instrument the aws-durable-execution-sdk-python library."""
    if getattr(aws_durable_execution_sdk_python, "_datadog_patch", False):
        return

    aws_durable_execution_sdk_python._datadog_patch = True

    # --- durable_execution decorator ---
    # AIDEV-NOTE: durable_execution is re-exported from the top-level __init__
    # via ``from .execution import durable_execution``. That creates a separate
    # name binding, so we must wrap in BOTH modules.
    wrap("aws_durable_execution_sdk_python.execution", "durable_execution", _traced_durable_execution)
    wrap("aws_durable_execution_sdk_python", "durable_execution", _traced_durable_execution)

    # --- DurableContext methods (always present) ---
    wrap("aws_durable_execution_sdk_python.context", "DurableContext.step", _traced_step)
    wrap("aws_durable_execution_sdk_python.context", "DurableContext.invoke", _traced_invoke)

    # --- DurableContext methods (may not exist in older SDK versions) ---
    _try_wrap("aws_durable_execution_sdk_python.context", "DurableContext.wait", _traced_wait, "DurableContext.wait")
    _try_wrap(
        "aws_durable_execution_sdk_python.context",
        "DurableContext.wait_for_condition",
        _traced_wait_for_condition,
        "DurableContext.wait_for_condition",
    )
    _try_wrap(
        "aws_durable_execution_sdk_python.context",
        "DurableContext.wait_for_callback",
        _traced_wait_for_callback,
        "DurableContext.wait_for_callback",
    )
    _try_wrap(
        "aws_durable_execution_sdk_python.context",
        "DurableContext.create_callback",
        _traced_create_callback,
        "DurableContext.create_callback",
    )
    _try_wrap("aws_durable_execution_sdk_python.context", "DurableContext.map", _traced_map, "DurableContext.map")
    _try_wrap(
        "aws_durable_execution_sdk_python.context",
        "DurableContext.parallel",
        _traced_parallel,
        "DurableContext.parallel",
    )
    _try_wrap(
        "aws_durable_execution_sdk_python.context",
        "DurableContext.run_in_child_context",
        _traced_run_in_child_context,
        "DurableContext.run_in_child_context",
    )

    # --- ThreadPoolExecutor patching for map/parallel trace propagation ---
    try:
        import aws_durable_execution_sdk_python.concurrency.executor as executor_module
        executor_module.ThreadPoolExecutor = TracedThreadPoolExecutor
    except (ImportError, AttributeError):
        log.debug("Could not patch ThreadPoolExecutor (concurrency module may not exist)")


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

    # Unwrap optional methods — only if they were wrapped (i.e. have __wrapped__)
    for method_name in (
        "wait",
        "wait_for_condition",
        "wait_for_callback",
        "create_callback",
        "map",
        "parallel",
        "run_in_child_context",
    ):
        method = getattr(DurableContext, method_name, None)
        if method is not None and hasattr(method, "__wrapped__"):
            unwrap(DurableContext, method_name)

    # Restore original ThreadPoolExecutor
    try:
        import aws_durable_execution_sdk_python.concurrency.executor as executor_module
        executor_module.ThreadPoolExecutor = _OriginalThreadPoolExecutor
    except (ImportError, AttributeError):
        pass
