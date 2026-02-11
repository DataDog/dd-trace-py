from typing import Dict

class TagsetDecodeError(ValueError): ...
class TagsetEncodeError(ValueError): ...

class TagsetMaxSizeEncodeError(TagsetEncodeError):
    values: Dict[str, str]  # noqa: UP006
    max_size: int
    current_results: str
    def __init__(self, values: Dict[str, str], max_size: int, current_results: str): ...  # noqa: UP006

class TagsetMaxSizeDecodeError(TagsetDecodeError):
    value: Dict[str, str]  # noqa: UP006
    max_size: int
    def __init__(self, value: Dict[str, str], max_size: int): ...  # noqa: UP006

def decode_tagset_string(tagset: str) -> Dict[str, str]: ...  # noqa: UP006
def encode_tagset_values(values: Dict[str, str], max_size: int = 512) -> str: ...  # noqa: UP006
