import typing


if typing.TYPE_CHECKING:
    from typing import Dict
    from typing import List


def expand(data):
    if isinstance(data, list):
        return {str(i): completion for i, completion in enumerate(data)}
    return data


def summarize_embedding(data):
    try:
        return {
            "num-embeddings": len(data),
            "embedding-length": len(data[0]["embedding"]),
        }
    except (KeyError, IndexError):
        return {}


def append_tag_prefixes(key_prefixes, data):
    # type: (List[str], Dict[str, str]) -> Dict[str, str]
    prefix = ".".join(key_prefixes) + "."
    return [(prefix + str(k) + ".", v) for k, v in data.items()]


def process_text(text):
    if isinstance(text, str):
        text = text.replace("\n", "\\n")
    return text
