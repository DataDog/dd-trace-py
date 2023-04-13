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
        # request field where model output is stored
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
        # request field where model output is stored
        "model_output": "choices",
        "output_processor": expand,
    },
}
