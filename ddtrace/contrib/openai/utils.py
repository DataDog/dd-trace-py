import re
from typing import Optional


_punc_regex = re.compile(r"[\w']+|[.,!?;~@#$%^&*()+/-]")


def _est_tokens(s):
    # type: (str) -> int
    """Provide a very rough estimate of the number of tokens.

    Approximate using the following assumptions:
        * English text
        * 1 token ~= 4 chars
        * 1 token ~= ¾ words

    Note that this function is 3x faster than tiktoken's encoding.
    """
    est1 = len(s) / 4
    est2 = len(_punc_regex.findall(s)) * 0.75
    est = round((1.5 * est1 + 0.5 * est2) / 2)
    return est


def _format_openai_api_key(openai_api_key):
    # type: (Optional[str]) -> Optional[str]
    """
    Returns `sk-...XXXX`, where XXXX is the last 4 characters of the provided OpenAI API key.
    This mimics how OpenAI UI formats the API key.
    """
    if not openai_api_key:
        return None
    return "sk-...%s" % openai_api_key[-4:]


def _set_openai_api_key_tag(span, args, kwargs, api_key_index=None):
    """
    Given a specified arg index for the api_key argument, this helper takes the api_key from the provided args/kwargs
    (in that priority) and sets it as a span tag. If the retrieved api_key value is None (this happens when the api key
    is set as an environment variable), this method will do nothing.
    """
    if api_key_index and api_key_index < len(args):
        openai_api_key = args[api_key_index]
    else:
        openai_api_key = kwargs.get("api_key", "")
    if not openai_api_key:
        return
    span.set_tag_str("openai.user.api_key", _format_openai_api_key(openai_api_key))
    return
