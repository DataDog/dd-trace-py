"""Tag keys and span context item names shared by APM-side LLM instrumentation and LLM Observability.

They live here rather than in ddtrace.llmobs so contrib and tracing code can tag APM spans for LLM
requests without depending on the LLM Observability product.
"""

UNKNOWN_MODEL_PROVIDER = "unknown"
UNKNOWN_MODEL_NAME = "unknown"

INPUT_TOKENS_METRIC_KEY = "input_tokens"
OUTPUT_TOKENS_METRIC_KEY = "output_tokens"
TOTAL_TOKENS_METRIC_KEY = "total_tokens"
CACHE_WRITE_INPUT_TOKENS_METRIC_KEY = "cache_write_input_tokens"
CACHE_READ_INPUT_TOKENS_METRIC_KEY = "cache_read_input_tokens"
REASONING_OUTPUT_TOKENS_METRIC_KEY = "reasoning_output_tokens"
CACHE_WRITE_1H_INPUT_TOKENS_METRIC_KEY = "ephemeral_1h_input_tokens"
CACHE_WRITE_5M_INPUT_TOKENS_METRIC_KEY = "ephemeral_5m_input_tokens"

LLMOBS_APM_SHADOW_INPUT_TOKENS_METRIC_KEY = "_dd.llmobs.input_tokens"
LLMOBS_APM_SHADOW_OUTPUT_TOKENS_METRIC_KEY = "_dd.llmobs.output_tokens"
LLMOBS_APM_SHADOW_TOTAL_TOKENS_METRIC_KEY = "_dd.llmobs.total_tokens"
LLMOBS_APM_SHADOW_CACHE_READ_INPUT_TOKENS_METRIC_KEY = "_dd.llmobs.cache_read_input_tokens"
LLMOBS_APM_SHADOW_CACHE_WRITE_INPUT_TOKENS_METRIC_KEY = "_dd.llmobs.cache_write_input_tokens"
LLMOBS_APM_SHADOW_SPAN_KIND_TAG_KEY = "_dd.llmobs.span_kind"
LLMOBS_APM_SHADOW_MODEL_NAME_TAG_KEY = "_dd.llmobs.model_name"
LLMOBS_APM_SHADOW_MODEL_PROVIDER_TAG_KEY = "_dd.llmobs.model_provider"
LLMOBS_APM_SHADOW_ENABLED_METRIC_KEY = "_dd.llmobs.enabled"
LLMOBS_ARTIFICIAL_GEN_AI_TAGS_KEY = "_dd.llmobs.artificial_gen_ai_tags"

GEN_AI_OPERATION_NAME_TAG_KEY = "gen_ai.operation.name"
GEN_AI_REQUEST_MODEL_TAG_KEY = "gen_ai.request.model"
GEN_AI_PROVIDER_NAME_TAG_KEY = "gen_ai.provider.name"
GEN_AI_APPLICATION_NAME_TAG_KEY = "gen_ai.application.name"
GEN_AI_CONVERSATION_ID_TAG_KEY = "gen_ai.conversation.id"

GEN_AI_USAGE_INPUT_TOKENS_METRIC_KEY = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS_METRIC_KEY = "gen_ai.usage.output_tokens"
GEN_AI_USAGE_TOTAL_TOKENS_METRIC_KEY = "gen_ai.usage.total_tokens"
GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS_METRIC_KEY = "gen_ai.usage.cache_read_input_tokens"
GEN_AI_USAGE_CACHE_WRITE_INPUT_TOKENS_METRIC_KEY = "gen_ai.usage.cache_write_input_tokens"
GEN_AI_USAGE_REASONING_OUTPUT_TOKENS_METRIC_KEY = "gen_ai.usage.reasoning_output_tokens"

PROXY_REQUEST = "llmobs.proxy_request"

REQUEST_BASE_URL = "llmobs.request_base_url"
