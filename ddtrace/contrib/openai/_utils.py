import typing


if typing.TYPE_CHECKING:
    from typing import Dict
    from typing import List

# SUPPORTED_ENGINES specifies the engines we support custom span formatting
# for request/response data.
SUPPORTED_ENGINES = {"ChatCompletions": "chat.completions", "Completions": "completions", "Embeddings": "embeddings"}

# See models and endpoints at: https://platform.openai.com/docs/models/model-endpoint-compatibility
# See pricing at: https://openai.com/pricing
# PRICING details the $ price per 1k tokens.
PRICING = {
    "chat.completions": {
        "gpt-4": {"prompt": 0.03, "completion": 0.06},
        "gpt-4-32k": {"prompt": 0.06, "completion": 0.12},
        "gpt-3.5-turbo": {"prompt": 0.002, "completion": 0.002},
    },
    "completions": {
        "text-davinci-003": 0.02,
        "text-davinci-002": 0.02,
        "text-curie-001": 0.002,
        "text-babbage-001": 0.0005,
        "text-ada-001": 0.0004,
        "davinci": 0.02,
        "curie": 0.002,
        "babbage": 0.0005,
        "ada": 0.004,
    },
    "embeddings": {"text-embedding-ada-002": 0.0004, "text-search-ada-doc-001": 0.0004},
}

# ENGINE_ARGUMENTS specifies request/response endpoint fields
# we want to parse and store in spans.
ENGINE_ARGUMENTS = {
    SUPPORTED_ENGINES["ChatCompletions"]: {
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
    },
    SUPPORTED_ENGINES["Completions"]: {
        "request": [
            "model",
            "suffix" "max_tokens",
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
        ],
        "response": ["id", "object", "created", "choices", "usage"],
    },
    SUPPORTED_ENGINES["Embeddings"]: {
        "request": ["model", "input", "user"],
        "response": ["model" "data", "object", "usage"],
    },
}


def append_tag_prefixes(key_prefixes, data):
    # type: (List[str], Dict[str, str]) -> Dict[str, str]
    prefix = ".".join(key_prefixes) + "."
    return [(prefix + str(k), v) for k, v in data.items()]


def expand(data):
    if isinstance(data, list):
        return {str(i): completion for i, completion in enumerate(data)}
    return data


def update_engine_names(openai):
    if hasattr(openai, "ChatCompletion") and hasattr(openai.ChatCompletion, "OBJECT_NAME"):
        SUPPORTED_ENGINES["ChatCompletions"] == openai.ChatCompletion.OBJECT_NAME
    if hasattr(openai, "Completion") and hasattr(openai.Completion, "OBJECT_NAME"):
        SUPPORTED_ENGINES["Completions"] == openai.Completion.OBJECT_NAME
    if hasattr(openai, "Embeddings") and hasattr(openai.Embeddings, "OBJECT_NAME"):
        SUPPORTED_ENGINES["Embeddings"] == openai.Embeddings.OBJECT_NAME


def process_text(text):
    if isinstance(text, str):
        text = text.replace("\n", "\\n")
    return text


def infer_object_name(kwargs):
    if kwargs.get("messages") is not None:
        return "chat.completions"
    elif kwargs.get("input") is not None:
        return "embeddings"
    elif kwargs.get("prompt") is not None:
        return "completions"
    return "<default>"


def process_response(openai, engine, resp):
    # alter the response tag value based on the `engine`
    # (completions, chat.completions, embeddings)
    try:
        update_engine_names(openai)
        resp = openai.util.convert_to_dict(resp)
        ret = {}
        for arg in ENGINE_ARGUMENTS[engine]["response"]:
            if arg == "data" and engine == SUPPORTED_ENGINES["Embeddings"]:
                ret["data"] = {
                    "num-embeddings": len(resp["data"]),
                    "embedding-length": len(resp["data"][0]["embedding"]),
                }
            elif arg == "choices":
                ret["choices"] = expand(resp.get("choices"))
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
        input_data_arg = (
            "messages"
            if engine == SUPPORTED_ENGINES["ChatCompletions"]
            else "prompt"
            if engine == SUPPORTED_ENGINES["Completions"]
            else "input"
            if engine == SUPPORTED_ENGINES["Embeddings"]
            else None
        )
        for arg in ENGINE_ARGUMENTS[engine]["request"]:
            if input_data_arg and arg == input_data_arg:
                request[arg] = expand(kwargs.get(arg))
            else:
                if kwargs.get(arg):
                    request[arg] = kwargs.get(arg)
        # to-do - need to investigate what actually shows up in `args` when
        return request
    except KeyError or IndexError:
        return {}


def get_price(model_name, num_tokens, engine, token_type="prompt"):
    try:
        engine_pricing_info = PRICING.get(engine)
        if engine_pricing_info:
            price = engine_pricing_info.get(model_name)
            if price:
                return num_tokens * (price if engine != "chat.completions" else price[token_type])
            else:
                for model, price in engine_pricing_info.items():
                    # openai offers model version snapshots that stay static for 3 month periods
                    # e.g. gpt-3.5-turbo-0301 is a snapshot from March 1st that will deprecate on June 1st.
                    if model_name.startswith(model):
                        return num_tokens * (price if engine != "chat.completions" else price[token_type])
        return 0
    except KeyError:
        return 0
