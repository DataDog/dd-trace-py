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

DECISION_FUNCTIONS = ("decisions", "adecisions", "systemone", "asystemone")
DECISION_OPERATIONS = DECISION_FUNCTIONS + tuple(f"router.{name}" for name in DECISION_FUNCTIONS)
SYSTEM_ONE_OPERATIONS = ("systemone", "asystemone", "router.systemone", "router.asystemone")

# Maps requested model name to (parsed model name, provider), filled in as litellm resolves providers.
model_map: dict[str, tuple[str, str]] = {}
