from typing import Final


SESSION_ID = "_ml_obs.session_id"
ML_APP = "_ml_obs.meta.ml_app"
ML_APP_DEFAULT = "unnamed-ml-app"
PROPAGATED_PARENT_ID_KEY = "_dd.p.llmobs_parent_id"
LLMOBS_SUBMITTED_TAG_KEY = "_dd.llmobs.submitted"
PROPAGATED_ML_APP_KEY = "_dd.p.llmobs_ml_app"
PROPAGATED_LLMOBS_TRACE_ID_KEY = "_dd.p.llmobs_trace_id"
LLMOBS_TRACE_ID = "_ml_obs.llmobs_trace_id"  # Deprecated: use get_llmobs_trace_id() from ddtrace.llmobs._utils

UNKNOWN_MODEL_PROVIDER = "unknown"
UNKNOWN_MODEL_NAME = "unknown"

INPUT_PROMPT = "_ml_obs.meta.input.prompt"

SPAN_START_WHILE_DISABLED_WARNING = (
    "Span started with LLMObs disabled."
    " If using ddtrace-run, ensure DD_LLMOBS_ENABLED is set to 1. Else, use LLMObs.enable()."
    " See https://docs.datadoghq.com/llm_observability/setup/sdk/python/#setup."
)

CLAUDE_AGENT_SDK_APM_SPAN_NAME = "claude_agent_sdk.request"
CREWAI_APM_SPAN_NAME = "crewai.request"
GEMINI_APM_SPAN_NAME = "gemini.request"
LANGCHAIN_APM_SPAN_NAME = "langchain.request"
LITELLM_APM_SPAN_NAME = "litellm.request"
OPENAI_APM_SPAN_NAME = "openai.request"
VERTEXAI_APM_SPAN_NAME = "vertexai.request"

INPUT_TOKENS_METRIC_KEY = "input_tokens"
OUTPUT_TOKENS_METRIC_KEY = "output_tokens"
TOTAL_TOKENS_METRIC_KEY = "total_tokens"
CACHE_WRITE_INPUT_TOKENS_METRIC_KEY = "cache_write_input_tokens"
CACHE_READ_INPUT_TOKENS_METRIC_KEY = "cache_read_input_tokens"
BILLABLE_CHARACTER_COUNT_METRIC_KEY = "billable_character_count"
REASONING_OUTPUT_TOKENS_METRIC_KEY = "reasoning_output_tokens"
CACHE_WRITE_1H_INPUT_TOKENS_METRIC_KEY = "ephemeral_1h_input_tokens"
CACHE_WRITE_5M_INPUT_TOKENS_METRIC_KEY = "ephemeral_5m_input_tokens"

TIME_TO_FIRST_TOKEN_METRIC_KEY = "time_to_first_token"  # nosec B105
TIME_IN_QUEUE_METRIC_KEY = "time_in_queue"
TIME_IN_MODEL_PREFILL_METRIC_KEY = "time_in_model_prefill"
TIME_IN_MODEL_DECODE_METRIC_KEY = "time_in_model_decode"
TIME_IN_MODEL_INFERENCE_METRIC_KEY = "time_in_model_inference"
# TIME_E2E_METRIC_KEY = "time_e2e"

EVAL_ENDPOINT = "/api/intake/llm-obs/v2/eval-metric"
SPAN_ENDPOINT = "/api/v2/llmobs"
SPAN_SUBDOMAIN_NAME = "llmobs-intake"
EVAL_SUBDOMAIN_NAME = "api"
EXP_SUBDOMAIN_NAME = "api"
AGENTLESS_SPAN_BASE_URL = "https://{}".format(SPAN_SUBDOMAIN_NAME)
AGENTLESS_EVAL_BASE_URL = "https://{}".format(EVAL_SUBDOMAIN_NAME)
AGENTLESS_EXP_BASE_URL = "https://{}".format(EXP_SUBDOMAIN_NAME)

# from https://docs.datadoghq.com/getting_started/site/#access-the-datadog-site
DD_SITES_NEEDING_APP_SUBDOMAIN = {"datadoghq.com", "datadoghq.eu", "ddog-gov.com"}
DD_SITE_STAGING = "datad0g.com"

EXPERIMENT_CSV_FIELD_MAX_SIZE = 10 * 1024 * 1024

DROPPED_IO_COLLECTION_ERROR = "dropped_io"
DROPPED_VALUE_TEXT = "[This value has been dropped because this span's size exceeds the 5MB size limit.]"

ROOT_PARENT_ID = "undefined"

ANNOTATIONS_CONTEXT_ID = "annotations_context_id"
INTERNAL_CONTEXT_VARIABLE_KEYS = "_dd_context_variable_keys"
INTERNAL_QUERY_VARIABLE_KEYS = "_dd_query_variable_keys"

# Prompt constants
DEFAULT_PROMPT_NAME = "unnamed-prompt"

# Prompt tracking tags
PROMPT_TRACKING_INSTRUMENTATION_METHOD = "prompt_tracking_instrumentation_method"
PROMPT_MULTIMODAL = "prompt_multimodal"
INSTRUMENTATION_METHOD_AUTO = "auto"
INSTRUMENTATION_METHOD_ANNOTATED = "annotated"

DISPATCH_ON_TOOL_CALL_OUTPUT_USED = "on_tool_call_output_used"
DISPATCH_ON_LLM_TOOL_CHOICE = "on_llm_tool_choice"
DISPATCH_ON_TOOL_CALL = "on_tool_call"

DISPATCH_ON_GUARDRAIL_SPAN_START = "on_guardrail_span_start"
DISPATCH_ON_LLM_SPAN_FINISH = "on_llm_span_finish"
DISPATCH_ON_OPENAI_AGENT_SPAN_FINISH = "on_openai_agent_span_finish"

# Tool call arguments are used to lookup the associated tool call info.
# When there are no tool call args, we use this as a place-holder lookup key
OAI_HANDOFF_TOOL_ARG = "{}"

LITELLM_ROUTER_INSTANCE_KEY = "_dd.router_instance"

PROXY_REQUEST = "llmobs.proxy_request"

# experiment span baggage keys to be propagated across boundaries
EXPERIMENT_ID_KEY = "_ml_obs.experiment_id"
EXPERIMENT_RUN_ID_KEY = "_ml_obs.experiment_run_id"
EXPERIMENT_RUN_ITERATION_KEY = "_ml_obs.experiment_run_iteration"
EXPERIMENT_PROJECT_NAME_KEY = "_ml_obs.experiment_project_name"
EXPERIMENT_PROJECT_ID_KEY = "_ml_obs.experiment_project_id"
EXPERIMENT_DATASET_NAME_KEY = "_ml_obs.experiment_dataset_name"
EXPERIMENT_NAME_KEY = "_ml_obs.experiment_name"

# experiment context keys
DEFAULT_PROJECT_NAME = "default-project"

# Fallback markers for prompt tracking when OpenAI strips values
IMAGE_FALLBACK_MARKER = "[image]"
FILE_FALLBACK_MARKER = "[file]"

# OpenAI input types
INPUT_TYPE_IMAGE = "input_image"
INPUT_TYPE_FILE = "input_file"
INPUT_TYPE_TEXT = "input_text"

# Managed Prompts Cache and Timeout defaults
DEFAULT_PROMPTS_CACHE_TTL = 60  # seconds before stale
DEFAULT_PROMPTS_TIMEOUT = 5.0  # seconds for all prompt fetch operations

# Managed Prompts API
PROMPTS_ENDPOINT = "/api/unstable/llm-obs/v1/prompts"


class LLMOBS_STRUCT:
    """Nested LLMObs struct keys in span._meta_struct."""

    KEY: Final = "_llmobs"
    NAME: Final = "name"
    PARENT_ID: Final = "parent_id"
    TRACE_ID: Final = "trace_id"
    ML_APP: Final = "ml_app"
    SESSION_ID: Final = "session_id"
    TAGS: Final = "tags"
    COST_TAGS: Final = "cost_tags"
    INTEGRATION: Final = "integration"
    PROMPT: Final = "prompt"
    METRICS: Final = "metrics"
    METADATA: Final = "metadata"
    METADATA_DD: Final = "_dd"
    SPAN_LINKS: Final = "span_links"
    META: Final = "meta"
    ERROR: Final = "error"
    TOOL_DEFINITIONS: Final = "tool_definitions"
    INPUT: Final = "input"
    OUTPUT: Final = "output"
    EXPECTED_OUTPUT: Final = "expected_output"
    VALUE: Final = "value"
    MESSAGES: Final = "messages"
    DOCUMENTS: Final = "documents"
    AGENT_MANIFEST: Final = "agent_manifest"
    SPAN: Final = "span"
    KIND: Final = "kind"
    MODEL_NAME: Final = "model_name"
    MODEL_PROVIDER: Final = "model_provider"
    INTENT: Final = "intent"
    CONFIG: Final = "config"


SUPPORTED_LLMOBS_INTEGRATIONS: dict[str, str] = {
    "anthropic": "anthropic",
    "bedrock": "botocore",
    "openai": "openai",
    "langchain": "langchain",
    "google_adk": "google_adk",
    "google_genai": "google_genai",
    "vertexai": "vertexai",
    "langgraph": "langgraph",
    "litellm": "litellm",
    "crewai": "crewai",
    "openai_agents": "openai_agents",
    "mcp": "mcp",
    "pydantic_ai": "pydantic_ai",
    "claude_agent_sdk": "claude_agent_sdk",
}

# Deprecated constants kept for backwards compatibility with downstream consumers of ddtrace internals.
# These were removed in the span._store -> span._meta_struct migration (PR #16774).
EXPERIMENT_RECORD_METADATA = "_ml_obs.meta.metadata"
EXPERIMENT_EXPECTED_OUTPUT = "_ml_obs.meta.input.expected_output"
