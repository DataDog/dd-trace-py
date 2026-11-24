COMPONENT = "litellm"
TAG_MODEL = "litellm.request.model"
TAG_HOST = "litellm.request.host"
TAG_RESPONSE_MODEL = "litellm.response.model"
# Request kwargs key carrying the Router instance, so LLMObs can report the router settings.
ROUTER_INSTANCE_KEY = "_dd.router_instance"
# Request kwargs key set when the request kwargs are those of the fallback attempt that answered the call. Its value
# tells whether the OpenAI integration emitted the LLM span for that attempt.
FALLBACK_ATTEMPT_KEY = "_dd.fallback_attempt"

TEXT_COMPLETION_OPERATIONS = (
    "text_completion",
    "atext_completion",
    "router.text_completion",
    "router.atext_completion",
)

# Maps requested model name to (parsed model name, provider), filled in as litellm resolves providers.
model_map: dict[str, tuple[str, str]] = {}
