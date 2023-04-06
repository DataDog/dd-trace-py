import openai


def append_tag_prefixes(key_prefixes: list[str], data: dict):
    prefix = ".".join(key_prefixes) + "."
    return [(prefix + str(k), v) for k, v in data.items()]


def expand(data):
    if isinstance(data, list):
        return {str(i): dict(completion) for i, completion in enumerate(data)}
    return data


def process_response(engine, resp):
    # alter the response tag value based on the `engine`
    # (completions, chat.completions, embeddings)
    try:
        resp = openai.util.convert_to_dict(resp)
        if engine in (openai.ChatCompletion.OBJECT_NAME, openai.Completion.OBJECT_NAME):
            resp["choices"] = expand(resp["choices"])
            return resp
        elif engine == openai.Embedding.OBJECT_NAME:
            if "data" in resp:
                resp["data"] = {
                    "num-embeddings": len(resp["data"]),
                    "embedding-length": len(resp["data"][0]["embedding"]),
                }
                return resp
    except KeyError or IndexError:
        return {}


def process_request(engine, args, kwargs):
    try:
        if engine in [openai.Completion.OBJECT_NAME, openai.Embedding.OBJECT_NAME]:
            return {**kwargs, **{k: v for k, v in args}}
        elif engine == openai.ChatCompletion.OBJECT_NAME:
            kwargs = kwargs.copy()
            messages = kwargs.get("messages")
            # messages are a series of messages each defined by a `role` and `content`
            kwargs["messages"] = expand(messages)
            return {**kwargs, **{k: v for k, v in args}}
    except KeyError or IndexError:
        return {}
