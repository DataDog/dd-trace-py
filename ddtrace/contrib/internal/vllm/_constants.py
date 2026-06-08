SPAN_NAME = "vllm.request"
OPERATION_ID = "vllm.request"

TAG_MODEL = "vllm.request.model"
TAG_PROVIDER = "vllm.request.provider"
PROVIDER_NAME = "vllm"

METRIC_LATENCY_TTFT = "vllm.latency.ttft"
METRIC_LATENCY_QUEUE = "vllm.latency.queue"
METRIC_LATENCY_PREFILL = "vllm.latency.prefill"
METRIC_LATENCY_DECODE = "vllm.latency.decode"
METRIC_LATENCY_INFERENCE = "vllm.latency.inference"

MIN_VERSION = "0.10.2"

# vLLM >= 0.14.0 renamed vllm.v1.engine.processor.Processor to
# vllm.v1.engine.input_processor.InputProcessor. We probe the new module first
# and fall back to the legacy one so the integration keeps working across the
# declared support window (vLLM >= 0.10.2).
PROCESSOR_MODULE_NEW = "vllm.v1.engine.input_processor"
PROCESSOR_CLASS_NEW = "InputProcessor"
PROCESSOR_MODULE_OLD = "vllm.v1.engine.processor"
PROCESSOR_CLASS_OLD = "Processor"
PROCESSOR_METHOD = "process_inputs"

ATTR_MODEL_NAME = "_dd_model_name"
ATTR_DATADOG_PATCH = "_datadog_patch"
ATTR_DATADOG_INTEGRATION = "_datadog_integration"

ARG_POSITION_LOG_STATS = 2
ARG_POSITION_TRACE_HEADERS = 6
