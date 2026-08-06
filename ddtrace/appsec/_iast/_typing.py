from typing import TypeVar
from typing import Union


TextType = Union[str, bytes, bytearray]
TextTypeT = TypeVar("TextTypeT", str, bytes, bytearray)
