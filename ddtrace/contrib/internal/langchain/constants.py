# LLMObs subscribes to shared lifecycle events for this component; see ddtrace/llmobs/_contrib/langchain.
COMPONENT = "langchain"

# Keep APM model/provider tags available even without LLMObs subscriber hooks.
MODEL_TAG = "langchain.request.model"
PROVIDER_TAG = "langchain.request.provider"
STREAM_TAG = "langchain.request.stream"

# Operation names carried on LlmRequestEvent; LangChainIntegration dispatches LLMObs tagging on these.
OPERATION_LLM = "llm"
OPERATION_CHAT = "chat"
OPERATION_CHAIN = "chain"
OPERATION_EMBEDDING = "embedding"
OPERATION_RETRIEVAL = "retrieval"
OPERATION_TOOL = "tool"
OPERATION_RUNNABLE_LAMBDA = "runnable_lambda"
OPERATION_DECISION = "decision"

# Key under which non-streamed chain inputs are passed in LlmRequestEvent.request_kwargs.
CHAIN_INPUTS_KEY = "inputs"

# Dispatched with the stream's span and a per-pull state dict around each chunk pull, so LLMObs can
# activate the span for the pull and restore the caller's LLMObs context afterwards.
STREAM_PULL_BEFORE_EVENT = "langchain.stream.pull.before"
STREAM_PULL_AFTER_EVENT = "langchain.stream.pull.after"
