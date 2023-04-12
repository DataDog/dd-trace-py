from ._utils import expand
from ._utils import summarize_embedding


# endpoints we support custom span formatting for request/response data.
EMBEDDINGS = "embeddings"
COMPLETIONS = "completions"
CHAT_COMPLETIONS = "chat.completions"


# ENDPOINT_DATA specifies request/response endpoint fields
# we want to parse and store in spans.
ENDPOINT_DATA = {
    CHAT_COMPLETIONS: {
        "request": [
            "model",
            "top_p",
            "n",
            "stream",
            "stop",
            "max_tokens",
            "presence_penalty",
            "frequency_penalty",
            "logit_bias",
            "messages",
        ],
        "response": ["id", "object", "created", "choices", "usage"],
        # request field where model input is stored
        "model_input": "messages",
        "model_output": "choices",
        "output_processor": expand,
    },
    COMPLETIONS: {
        "request": [
            "model",
            "suffix",
            "max_tokens",
            "temperature",
            "top_p",
            "n",
            "stream",
            "logprobs",
            "echo",
            "stop",
            "presence_penalty",
            "frequency_penalty",
            "best_of",
            "logit_bias",
            "user",
            "prompt",
        ],
        "response": ["id", "object", "created", "choices", "usage"],
        # request field where model input is stored
        "model_input": "prompt",
        "model_output": "choices",
        "output_processor": expand,
    },
    EMBEDDINGS: {
        "request": ["model", "input", "user"],
        "response": ["model" "data", "object", "usage"],
        # request field where model input is stored
        "model_input": "input",
        "model_output": "data",
        "output_processor": summarize_embedding,
    },
}


def update_engine_names(openai):
    if hasattr(openai, "ChatCompletion") and hasattr(openai.ChatCompletion, "OBJECT_NAME"):
        CHAT_COMPLETIONS == openai.ChatCompletion.OBJECT_NAME
    if hasattr(openai, "Completion") and hasattr(openai.Completion, "OBJECT_NAME"):
        COMPLETIONS == openai.Completion.OBJECT_NAME
    if hasattr(openai, "Embeddings") and hasattr(openai.Embeddings, "OBJECT_NAME"):
        EMBEDDINGS == openai.Embeddings.OBJECT_NAME


def supported(endpoint):
    import openai

    if endpoint == CHAT_COMPLETIONS:
        return hasattr(openai.api_resources, "chat_completion")
    elif endpoint == COMPLETIONS:
        return hasattr(openai.api_resources, "completion")
    elif endpoint == EMBEDDINGS:
        return hasattr(openai.api_resources, "embedding")
    return False


def process_response(openai, endpoint, resp):
    # alter the response tag value based on the `engine`
    # (completions, chat.completions, embeddings)
    try:
        update_engine_names(openai)
        resp = openai.util.convert_to_dict(resp)
        ret = {}
        for arg in ENDPOINT_DATA[endpoint]["response"]:
            if arg == ENDPOINT_DATA[endpoint]["model_output"]:
                ret[arg] = ENDPOINT_DATA[endpoint]["output_processor"](resp[arg])
            else:
                if resp.get(arg):
                    ret[arg] = resp.get(arg)
        return ret
    except KeyError or IndexError:
        return {}


def process_request(openai, engine, args, kwargs):
    try:
        update_engine_names(openai)
        request = {}
        for arg in ENDPOINT_DATA[engine]["request"]:
            if arg == ENDPOINT_DATA[engine]["model_input"]:
                request[arg] = expand(kwargs.get(arg))
            else:
                if kwargs.get(arg):
                    request[arg] = kwargs.get(arg)
        return request
    except KeyError or IndexError:
        return {}
