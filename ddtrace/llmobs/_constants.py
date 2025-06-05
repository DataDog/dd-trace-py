SPAN_KIND = "_ml_obs.meta.span.kind"
SESSION_ID = "_ml_obs.session_id"
METADATA = "_ml_obs.meta.metadata"
METRICS = "_ml_obs.metrics"
ML_APP = "_ml_obs.meta.ml_app"
PROPAGATED_PARENT_ID_KEY = "_dd.p.llmobs_parent_id"
PROPAGATED_ML_APP_KEY = "_dd.p.llmobs_ml_app"
PARENT_ID_KEY = "_ml_obs.llmobs_parent_id"
PROPAGATED_LLMOBS_TRACE_ID_KEY = "_dd.p.llmobs_trace_id"
LLMOBS_TRACE_ID = "_ml_obs.llmobs_trace_id"
TAGS = "_ml_obs.tags"

MODEL_NAME = "_ml_obs.meta.model_name"
MODEL_PROVIDER = "_ml_obs.meta.model_provider"

INPUT_DOCUMENTS = "_ml_obs.meta.input.documents"
INPUT_MESSAGES = "_ml_obs.meta.input.messages"
INPUT_VALUE = "_ml_obs.meta.input.value"
INPUT_PROMPT = "_ml_obs.meta.input.prompt"

OUTPUT_DOCUMENTS = "_ml_obs.meta.output.documents"
OUTPUT_MESSAGES = "_ml_obs.meta.output.messages"
OUTPUT_VALUE = "_ml_obs.meta.output.value"

SPAN_START_WHILE_DISABLED_WARNING = (
    "Span started with LLMObs disabled."
    " If using ddtrace-run, ensure DD_LLMOBS_ENABLED is set to 1. Else, use LLMObs.enable()."
    " See https://docs.datadoghq.com/llm_observability/setup/sdk/python/#setup."
)

GEMINI_APM_SPAN_NAME = "gemini.request"
LANGCHAIN_APM_SPAN_NAME = "langchain.request"
LITELLM_APM_SPAN_NAME = "litellm.request"
OPENAI_APM_SPAN_NAME = "openai.request"
VERTEXAI_APM_SPAN_NAME = "vertexai.request"
CREWAI_APM_SPAN_NAME = "crewai.request"

INPUT_TOKENS_METRIC_KEY = "input_tokens"
OUTPUT_TOKENS_METRIC_KEY = "output_tokens"
TOTAL_TOKENS_METRIC_KEY = "total_tokens"

EVP_PROXY_AGENT_BASE_PATH = "/evp_proxy/v2"
EVAL_ENDPOINT = "/api/intake/llm-obs/v2/eval-metric"
SPAN_ENDPOINT = "/api/v2/llmobs"
EVP_SUBDOMAIN_HEADER_NAME = "X-Datadog-EVP-Subdomain"
SPAN_SUBDOMAIN_NAME = "llmobs-intake"
EVAL_SUBDOMAIN_NAME = "api"
AGENTLESS_SPAN_BASE_URL = "https://{}".format(SPAN_SUBDOMAIN_NAME)
AGENTLESS_EVAL_BASE_URL = "https://{}".format(EVAL_SUBDOMAIN_NAME)

EVP_PAYLOAD_SIZE_LIMIT = 5 << 20  # 5MB (actual limit is 5.1MB)
EVP_EVENT_SIZE_LIMIT = (1 << 20) - 1024  # 999KB (actual limit is 1MB)


DROPPED_IO_COLLECTION_ERROR = "dropped_io"
DROPPED_VALUE_TEXT = "[This value has been dropped because this span's size exceeds the 1MB size limit.]"

ROOT_PARENT_ID = "undefined"

# Set for traces of evaluator integrations e.g. `runner.integration:ragas`.
# Used to differentiate traces of Datadog-run operations vs user-application operations.
RUNNER_IS_INTEGRATION_SPAN_TAG = "runner.integration"

# All ragas traces have this context item set so we can differentiate
# spans generated from the ragas integration vs user application spans.
IS_EVALUATION_SPAN = "_ml_obs.evaluation_span"

ANNOTATIONS_CONTEXT_ID = "annotations_context_id"
INTERNAL_CONTEXT_VARIABLE_KEYS = "_dd_context_variable_keys"
INTERNAL_QUERY_VARIABLE_KEYS = "_dd_query_variable_keys"

FAITHFULNESS_DISAGREEMENTS_METADATA = "_dd.faithfulness_disagreements"
EVALUATION_KIND_METADATA = "_dd.evaluation_kind"
EVALUATION_SPAN_METADATA = "_dd.evaluation_span"

SPAN_LINKS = "_ml_obs.span_links"
NAME = "_ml_obs.name"
DECORATOR = "_ml_obs.decorator"
INTEGRATION = "_ml_obs.integration"

DISPATCH_ON_TOOL_CALL_OUTPUT_USED = "on_tool_call_output_used"
DISPATCH_ON_LLM_TOOL_CHOICE = "on_llm_tool_choice"
DISPATCH_ON_TOOL_CALL = "on_tool_call"

# Tool call arguments are used to lookup the associated tool call info.
# When there are no tool call args, we use this as a place-holder lookup key
OAI_HANDOFF_TOOL_ARG = "{}"

LITELLM_ROUTER_INSTANCE_KEY = "_dd.router_instance"
