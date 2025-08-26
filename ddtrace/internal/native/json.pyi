from typing import Dict, List, Tuple, Union

# Supported JSON-serializable types
JSONValue = Union[None, bool, int, float, str, List["JSONValue"], Tuple["JSONValue", ...], Dict[str, "JSONValue"]]

# Fast JSON encoder for basic Python types
def dumps(__obj: JSONValue) -> str: ...
