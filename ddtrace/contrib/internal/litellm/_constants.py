COMPONENT = "litellm"
TAG_MODEL = "litellm.request.model"
TAG_HOST = "litellm.request.host"
TAG_RESPONSE_MODEL = "litellm.response.model"
# Request kwargs key carrying the Router instance, so LLMObs can report the router settings.
ROUTER_INSTANCE_KEY = "_dd.router_instance"

TEXT_COMPLETION_OPERATIONS = (
    "text_completion",
    "atext_completion",
    "router.text_completion",
    "router.atext_completion",
)
