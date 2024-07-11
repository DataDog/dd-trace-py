SPAN_KIND = "_ml_obs.meta.span.kind"
SESSION_ID = "_ml_obs.session_id"
METADATA = "_ml_obs.meta.metadata"
METRICS = "_ml_obs.metrics"
ML_APP = "_ml_obs.meta.ml_app"
PROPAGATED_PARENT_ID_KEY = "_dd.p.llmobs_parent_id"
PARENT_ID_KEY = "_ml_obs.llmobs_parent_id"
TAGS = "_ml_obs.tags"

MODEL_NAME = "_ml_obs.meta.model_name"
MODEL_PROVIDER = "_ml_obs.meta.model_provider"

INPUT_DOCUMENTS = "_ml_obs.meta.input.documents"
INPUT_MESSAGES = "_ml_obs.meta.input.messages"
INPUT_VALUE = "_ml_obs.meta.input.value"
INPUT_PARAMETERS = "_ml_obs.meta.input.parameters"

OUTPUT_DOCUMENTS = "_ml_obs.meta.output.documents"
OUTPUT_MESSAGES = "_ml_obs.meta.output.messages"
OUTPUT_VALUE = "_ml_obs.meta.output.value"

SPAN_START_WHILE_DISABLED_WARNING = (
    "Span started while LLMObs is disabled." " Spans will not be sent to LLM Observability."
)

LANGCHAIN_APM_SPAN_NAME = "langchain.request"
