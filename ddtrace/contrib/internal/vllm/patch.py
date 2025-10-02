from __future__ import annotations

import vllm

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations.vllm import VLLMIntegration

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
    # ALWAYS enable stats for tracing - we need req_state.stats.num_generation_tokens
    # log_stats is the 3rd positional arg (after vllm_config, executor_class)
    if len(args) > 2:
        args = args[:2] + (True,) + args[3:]
    else:
        kwargs["log_stats"] = True

    result = func(*args, **kwargs)

    if hasattr(instance, "model_config") and hasattr(instance, "output_processor"):
        model_name = getattr(instance.model_config, "model", None)
        if model_name:
            instance.output_processor._dd_model_name = model_name

    return result


@with_traced_module
def traced_processor_process_inputs(vllm, pin, func, instance, args, kwargs):
    """Inject Datadog trace context into trace_headers for propagation."""
    tracer = pin.tracer

    if len(args) > 6:
        trace_headers = args[6]
        injected_headers = inject_trace_context(tracer, trace_headers)
        args = args[:6] + (injected_headers,) + args[7:]
    else:
        trace_headers = kwargs.get("trace_headers")
        kwargs["trace_headers"] = inject_trace_context(tracer, trace_headers)

    return func(*args, **kwargs)


@with_traced_module
def traced_output_processor_process_outputs(vllm, pin, func, instance, args, kwargs):
    """Create Datadog spans for finished requests."""
    integration = vllm._datadog_integration

    engine_core_outputs = args[0] if args else kwargs.get("engine_core_outputs")

    if not engine_core_outputs:
        return func(*args, **kwargs)

    model_name = get_model_name(instance)

    # Capture req_states BEFORE calling func, as func will remove them
    spans_data = []
    for engine_core_output in engine_core_outputs:
        req_id = engine_core_output.request_id

        if not engine_core_output.finished:
            continue

        req_state = instance.request_states.get(req_id)
        if not req_state:
            continue

        # Extract all data we need before func() removes req_state
        arrival_time = req_state.stats.arrival_time if req_state.stats else None
        stats = req_state.stats
        data = extract_request_data(req_state, engine_core_output)

        spans_data.append(
            {
                "req_id": req_id,
                "trace_headers": engine_core_output.trace_headers,
                "arrival_time": arrival_time,
                "data": data,
                "stats": stats,
            }
        )

    # Now call the original function
    result = func(*args, **kwargs)

    # Create spans after original function completes
    for span_info in spans_data:
        span = create_span(
            pin=pin,
            integration=integration,
            model_name=model_name,
            trace_headers=span_info["trace_headers"],
            arrival_time=span_info["arrival_time"],
        )

        data = span_info["data"]
        operation = "embedding" if data.embedding_dim is not None else "completion"

        # Extract output_tokens from stats NOW (after original function updated it)
        if operation == "completion" and span_info["stats"]:
            data.output_tokens = span_info["stats"].num_generation_tokens

        integration.llmobs_set_tags(
            span,
            args=[],
            kwargs={"request_data": data},
            response=None,
            operation=operation,
        )

        if span_info["stats"]:
            set_latency_metrics(span, span_info["stats"])

        span.finish()

    return result


def patch():
    """Patch vLLM V1 library for Datadog tracing."""
    if getattr(vllm, "_datadog_patch", False):
        return

    # Check vLLM version - require >= 0.10.2 for V1 trace header propagation
    try:
        from packaging.version import parse as parse_version

        version_str = getattr(vllm, "__version__", "0.0.0")
        base_version = parse_version(version_str).base_version
        if parse_version(base_version) < parse_version("0.10.2"):
            logger.warning(
                "vLLM integration requires vLLM >= 0.10.2 for V1 engine support. "
                "Found version %s. Skipping instrumentation.",
                version_str,
            )
            return
    except Exception:
        logger.debug("Could not verify vLLM version, proceeding with instrumentation")

    vllm._datadog_patch = True

    Pin().onto(vllm)
    integration = VLLMIntegration(integration_config=config.vllm)
    vllm._datadog_integration = integration

    wrap("vllm.v1.engine.llm_engine", "LLMEngine.__init__", traced_engine_init(vllm))
    wrap("vllm.v1.engine.async_llm", "AsyncLLM.__init__", traced_engine_init(vllm))
    wrap("vllm.v1.engine.processor", "Processor.process_inputs", traced_processor_process_inputs(vllm))
    wrap(
        "vllm.v1.engine.output_processor",
        "OutputProcessor.process_outputs",
        traced_output_processor_process_outputs(vllm),
    )


def unpatch():
    if not getattr(vllm, "_datadog_patch", False):
        return

    vllm._datadog_patch = False

    unwrap(vllm.v1.engine.llm_engine.LLMEngine, "__init__")
    unwrap(vllm.v1.engine.async_llm.AsyncLLM, "__init__")
    unwrap(vllm.v1.engine.processor.Processor, "process_inputs")
    unwrap(vllm.v1.engine.output_processor.OutputProcessor, "process_outputs")

    delattr(vllm, "_datadog_integration")
