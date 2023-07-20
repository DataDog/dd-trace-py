import re
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

import tiktoken


_punc_regex = re.compile(r"[\w']+|[.,!?;~@#$%^&*()+/-]")


def _compute_prompt_token_count(prompt, model):
    # type: (Union[str, List[str], List[int], List[List[int]]], Optional[str]) -> Dict[str, Union[bool, int]]
    # TODO: need to account for str, str array, token arrays and array of token arrays
    metadata = {"num_prompt_tokens": 0, "estimated": False}

    if model is not None:
        try:
            enc = tiktoken.encoding_by_model(model)
        except KeyError:
            # tiktoken.encoding_by_model() will raise a KeyError if it doesn't have a tokenizer for the model
            pass
        if isinstance(prompt, str):
            metadata["num_prompt_tokens"] += len(enc.encode(prompt))

    # If model is unavailable or tiktoken is not imported, then provide a very rough estimate of the number of tokens
    # Approximate using the following assumptions:
    #    * English text
    #    * 1 token ~= 4 chars
    #    * 1 token ~= ¾ words
    est1 = len(prompt) / 4
    est2 = len(_punc_regex.findall(prompt)) * 0.75
    est = round((1.5 * est1 + 0.5 * est2) / 2)
    return est


def _est_tokens(prompt, model):
    # type: (str, Optional[str]) -> int
    """Use tiktoken to calculate the number of tokens used in a prompt."""
    if model is not None:
        enc = tiktoken.encoding_by_model(model)
        prompt_tokens = enc.encode(prompt)
        return len(prompt_tokens)

    # If model is unavailable or tiktoken is not imported, then provide a very rough estimate of the number of tokens
    # Approximate using the following assumptions:
    #    * English text
    #    * 1 token ~= 4 chars
    #    * 1 token ~= ¾ words
    est1 = len(prompt) / 4
    est2 = len(_punc_regex.findall(prompt)) * 0.75
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
