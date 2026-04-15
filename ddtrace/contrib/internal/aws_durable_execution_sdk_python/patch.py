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
import hashlib
import sys

import aws_durable_execution_sdk_python

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib import trace_utils
from ddtrace.contrib.internal.trace_utils import set_service_and_source
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.trace import tracer


log = get_logger(__name__)


config._add(
    "aws_durable_execution_sdk_python",
    dict(
        _default_service="aws_durable_execution_sdk_python",
    ),
)


def get_version() -> str:
    return getattr(aws_durable_execution_sdk_python, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"aws-durable-execution-sdk-python": ">=1.4.0"}


def _get_service():
    """Get the configured service name for this integration."""
    pin = Pin.get_from(aws_durable_execution_sdk_python)
    return trace_utils.int_service(pin, config.aws_durable_execution_sdk_python)


def _set_base_tags(span):
    """Set common tags on all spans from this integration."""
    set_service_and_source(span, _get_service(), config.aws_durable_execution_sdk_python)
    span._set_attribute(COMPONENT, config.aws_durable_execution_sdk_python.integration_name)


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
    def traced_user_func(input_event, durable_context):
        """Traced wrapper around the user's workflow function."""
        pin = Pin.get_from(aws_durable_execution_sdk_python)
        if not pin or not pin.enabled():
            return user_func(input_event, durable_context)

        # Lazy import to avoid errors when the SDK is not installed
        from aws_durable_execution_sdk_python.exceptions import SuspendExecution

        # --- Extract metadata from the DurableContext ---
        arn = None
        replay_status = None
        try:
            arn = getattr(durable_context.state, "durable_execution_arn", None)
        except Exception:
            pass
        try:
            status_enum = getattr(durable_context.state, "_replay_status", None)
            if status_enum is not None:
                replay_status = status_enum.name  # "NEW" or "REPLAY"
        except Exception:
            pass

        # --- Create the execution span ---
        span = tracer.trace(
            name="aws_durable_execution.execution",
            resource="aws_durable_execution.execution",
            span_type="serverless",
        )
        _set_base_tags(span)
        span._set_attribute("span.kind", "server")
        if arn:
            span._set_attribute("aws.durable_execution.arn", arn)
        if replay_status:
            span._set_attribute("aws.durable_execution.replay_status", replay_status)

        try:
            result = user_func(input_event, durable_context)
            span.finish()
            return result
        except SuspendExecution:
            # AIDEV-NOTE: SuspendExecution is control flow, not an error.
            span.finish()
            raise
        except BaseException:
            span.set_exc_info(*sys.exc_info())
            span.finish()
            raise

    # Replace user func with traced version, call the original decorator
    new_args = (traced_user_func,) + args[1:]
    return func(*new_args, **kwargs)


def _traced_step(func, instance, args, kwargs):
    """Wrapper for DurableContext.step().

    Creates a child span for each step execution with the step name,
    operation ID, and standard tags.
    """
    from aws_durable_execution_sdk_python.exceptions import SuspendExecution

    pin = Pin.get_from(aws_durable_execution_sdk_python)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    # --- Extract step name ---
    step_func = args[0] if args else None
    name_kwarg = kwargs.get("name")
    # Mirror the SDK's _resolve_step_name logic with __name__ fallback
    step_name = (
        name_kwarg
        or getattr(step_func, "_original_name", None)
        or getattr(step_func, "__name__", None)
    )

    # --- Predict the operation_id the SDK will generate ---
    # AIDEV-NOTE: The SDK increments an OrderedCounter and computes a
    # blake2b hash.  We peek at the counter before the SDK call to
    # pre-compute the same ID.  If internal APIs change, we fall back
    # gracefully.
    operation_id = None
    try:
        next_counter = instance._step_counter._counter + 1
        parent_id = instance._parent_id
        raw = "{}-{}".format(parent_id, next_counter) if parent_id else str(next_counter)
        operation_id = hashlib.blake2b(raw.encode()).hexdigest()[:64]
    except Exception:
        pass

    # --- Create the step span ---
    span = tracer.trace(
        name="aws_durable_execution.step",
        resource=step_name or "step",
        span_type="worker",
    )
    _set_base_tags(span)
    span._set_attribute("span.kind", "internal")
    if step_name:
        span._set_attribute("aws.durable_execution.step.name", step_name)
    if operation_id:
        span._set_attribute("aws.durable_execution.step.operation_id", operation_id)

    try:
        result = func(*args, **kwargs)
        span.finish()
        return result
    except SuspendExecution:
        span.finish()
        raise
    except BaseException:
        span.set_exc_info(*sys.exc_info())
        span.finish()
        raise


def _traced_invoke(func, instance, args, kwargs):
    """Wrapper for DurableContext.invoke().

    Creates a client span for cross-function invocations with the target
    function name and invocation name.
    """
    from aws_durable_execution_sdk_python.exceptions import SuspendExecution

    pin = Pin.get_from(aws_durable_execution_sdk_python)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    # --- Extract invoke parameters ---
    # invoke(function_name, payload, name=None, config=None)
    function_name = kwargs.get("function_name") or (args[0] if args else None)
    # payload is args[1] if positional
    invoke_name = kwargs.get("name")

    # --- Create the invoke span ---
    span = tracer.trace(
        name="aws_durable_execution.invoke",
        resource=invoke_name or function_name or "invoke",
        span_type="serverless",
    )
    _set_base_tags(span)
    span._set_attribute("span.kind", "client")
    if function_name:
        span._set_attribute("aws.durable_execution.invoke.function_name", str(function_name))
    if invoke_name:
        span._set_attribute("aws.durable_execution.invoke.name", invoke_name)

    try:
        result = func(*args, **kwargs)
        span.finish()
        return result
    except SuspendExecution:
        span.finish()
        raise
    except BaseException:
        span.set_exc_info(*sys.exc_info())
        span.finish()
        raise


def patch():
    """Instrument the aws-durable-execution-sdk-python library."""
    if getattr(aws_durable_execution_sdk_python, "_datadog_patch", False):
        return

    aws_durable_execution_sdk_python._datadog_patch = True
    Pin().onto(aws_durable_execution_sdk_python)

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
