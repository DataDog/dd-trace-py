from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any
from typing import Optional

import vllm

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations.vllm import VLLMIntegration
from ddtrace.trace import tracer


if TYPE_CHECKING:
    from vllm.v1.engine.output_processor import OutputProcessor

from ._constants import ARG_POSITION_LOG_STATS
from ._constants import ARG_POSITION_TRACE_HEADERS
from ._constants import ATTR_DATADOG_INTEGRATION
from ._constants import ATTR_DATADOG_PATCH
from ._constants import ATTR_MODEL_NAME
from ._constants import MIN_VERSION
from .extractors import extract_latency_metrics
from .extractors import extract_request_data
from .extractors import get_model_name
from .utils import create_span
from .utils import inject_trace_context
from .utils import set_latency_metrics


logger = get_logger(__name__)

config._add("vllm", {})


@with_traced_module
def traced_engine_init(vllm, pin, func, instance, args, kwargs):
    """Inject model name into OutputProcessor and force-enable stats for tracing."""
    # Force log_stats=True to enable vLLM's internal stats collection.
    # We need these stats for:
    # - output_tokens (num_generation_tokens from stats)
    # - latency metrics (TTFT, queue time, prefill, decode, inference)
    if len(args) > ARG_POSITION_LOG_STATS:
        args = args[:ARG_POSITION_LOG_STATS] + (True,) + args[ARG_POSITION_LOG_STATS + 1 :]
    else:
        kwargs["log_stats"] = True

    result = func(*args, **kwargs)

    if hasattr(instance, "model_config") and hasattr(instance, "output_processor"):
        model_name = getattr(instance.model_config, "model", None)
        if model_name:
            setattr(instance.output_processor, ATTR_MODEL_NAME, model_name)

    return result


@with_traced_module
def traced_processor_process_inputs(vllm, pin, func, instance, args, kwargs):
    """Inject Datadog trace context into trace_headers for propagation."""

    if len(args) > ARG_POSITION_TRACE_HEADERS:
        trace_headers = args[ARG_POSITION_TRACE_HEADERS]
        injected_headers = inject_trace_context(tracer, trace_headers)
        args = args[:ARG_POSITION_TRACE_HEADERS] + (injected_headers,) + args[ARG_POSITION_TRACE_HEADERS + 1 :]
    else:
        trace_headers = kwargs.get("trace_headers")
        kwargs["trace_headers"] = inject_trace_context(tracer, trace_headers)

    return func(*args, **kwargs)


def _capture_request_states(
    instance: "OutputProcessor",
    engine_core_outputs: Any,
) -> dict[str, dict[str, Any]]:
    """Capture request state data before original function removes them.

    Returns dict mapping request_id -> captured_data.
    """
    spans_data = {}
    for engine_core_output in engine_core_outputs:
        req_id = engine_core_output.request_id
        req_state = instance.request_states.get(req_id)

        if not req_state:
            continue

        spans_data[req_id] = {
            "trace_headers": engine_core_output.trace_headers,
            "arrival_time": req_state.stats.arrival_time if req_state.stats else None,
            "data": extract_request_data(req_state, engine_core_output),
            "stats": req_state.stats,
        }

    return spans_data


def _create_finished_spans(
    pin: Pin,
    integration: VLLMIntegration,
    model_name: Optional[str],
    instance: "OutputProcessor",
    spans_data: dict[str, dict[str, Any]],
) -> None:
    """Create and finish spans for completed requests."""
    for req_id, span_info in spans_data.items():
        if req_id in instance.request_states:
            continue

        span = create_span(
            pin=pin,
            integration=integration,
            model_name=model_name,
            trace_headers=span_info["trace_headers"],
            arrival_time=span_info["arrival_time"],
        )

        data = span_info["data"]
        operation = "embedding" if data.embedding_dim is not None else "completion"

        if operation == "completion" and span_info["stats"]:
            data.output_tokens = span_info["stats"].num_generation_tokens

        latency_metrics = extract_latency_metrics(span_info["stats"])

        integration.llmobs_set_tags(
            span,
            args=[],
            kwargs={
                "request_data": data,
                "latency_metrics": latency_metrics,
            },
            response=None,
            operation=operation,
        )

        set_latency_metrics(span, latency_metrics)
        span.finish()


@with_traced_module
def traced_output_processor_process_outputs(vllm, pin, func, instance, args, kwargs):
    """Create Datadog spans for finished requests."""
    integration = getattr(vllm, ATTR_DATADOG_INTEGRATION)

    engine_core_outputs = args[0] if args else kwargs.get("engine_core_outputs")

    if not engine_core_outputs:
        return func(*args, **kwargs)

    model_name = get_model_name(instance)
    spans_data = _capture_request_states(instance, engine_core_outputs)

    result = func(*args, **kwargs)

    _create_finished_spans(pin, integration, model_name, instance, spans_data)

    return result


def patch():
    """Patch vLLM V1 library for Datadog tracing."""
    if getattr(vllm, ATTR_DATADOG_PATCH, False):
        return

    try:
        from packaging.version import parse as parse_version

        version_str = getattr(vllm, "__version__", "0.0.0")
        base_version = parse_version(version_str).base_version
        if parse_version(base_version) < parse_version(MIN_VERSION):
            logger.warning(
                "vLLM integration requires vLLM >= %s for V1 engine support. "
                "Found version %s. Skipping instrumentation.",
                MIN_VERSION,
                version_str,
            )
            return
    except (ImportError, AttributeError) as e:
        logger.debug(
            "Could not verify vLLM version (missing packaging library or __version__): %s. "
            "Proceeding with instrumentation - may fail if version < %s",
            e,
            MIN_VERSION,
        )
    except Exception as e:
        logger.warning(
            "Unexpected error verifying vLLM version: %s. "
            "Proceeding with instrumentation, but compatibility issues may occur.",
            e,
            exc_info=True,
        )

    setattr(vllm, ATTR_DATADOG_PATCH, True)

    Pin().onto(vllm)
    integration = VLLMIntegration(integration_config=config.vllm)
    setattr(vllm, ATTR_DATADOG_INTEGRATION, integration)

    wrap("vllm.v1.engine.llm_engine", "LLMEngine.__init__", traced_engine_init(vllm))
    wrap("vllm.v1.engine.async_llm", "AsyncLLM.__init__", traced_engine_init(vllm))
    wrap("vllm.v1.engine.processor", "Processor.process_inputs", traced_processor_process_inputs(vllm))
    wrap(
        "vllm.v1.engine.output_processor",
        "OutputProcessor.process_outputs",
        traced_output_processor_process_outputs(vllm),
    )


def unpatch():
    if not getattr(vllm, ATTR_DATADOG_PATCH, False):
        return

    setattr(vllm, ATTR_DATADOG_PATCH, False)

    unwrap(vllm.v1.engine.llm_engine.LLMEngine, "__init__")
    unwrap(vllm.v1.engine.async_llm.AsyncLLM, "__init__")
    unwrap(vllm.v1.engine.processor.Processor, "process_inputs")
    unwrap(vllm.v1.engine.output_processor.OutputProcessor, "process_outputs")

    delattr(vllm, ATTR_DATADOG_INTEGRATION)
