import typing


if typing.TYPE_CHECKING:
    from typing import Dict
    from typing import List

# SUPPORTED_ENGINES specifies the engines we support custom span formatting
# for request/response data.
SUPPORTED_ENGINES = {"ChatCompletion": "chat.completions", "Completions": "completions", "Embeddings": "embeddings"}


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
        SUPPORTED_ENGINES["ChatCompletion"] == openai.ChatCompletion.OBJECT_NAME
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
        if engine in (SUPPORTED_ENGINES["ChatCompletion"], SUPPORTED_ENGINES["Completions"]):
            resp["choices"] = expand(resp["choices"])
            return resp
        elif engine == SUPPORTED_ENGINES["Embeddings"]:
            if "data" in resp:
                resp["data"] = {
                    "num-embeddings": len(resp["data"]),
                    "embedding-length": len(resp["data"][0]["embedding"]),
                }
                return resp
        else:
            return resp
    except KeyError or IndexError:
        return {}


def process_request(openai, engine, args, kwargs):
    try:
        update_engine_names(openai)
        kwargs = kwargs.copy()
        if engine == SUPPORTED_ENGINES["ChatCompletion"]:
            messages = kwargs.get("messages")
            # messages are a series of messages each defined by a `role` and `content`
            kwargs["messages"] = expand(messages)
        elif engine == SUPPORTED_ENGINES["Completions"]:
            prompt = kwargs.get("prompt")
            kwargs["prompt"] = expand(prompt)
        elif engine == SUPPORTED_ENGINES["Embeddings"]:
            inp = kwargs.get("input")
            kwargs["input"] = expand(inp)
        return {**kwargs, **{k: v for k, v in args}}
    except KeyError or IndexError:
        return {}
