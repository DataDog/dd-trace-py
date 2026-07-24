from typing import Any
from typing import Optional

from ddtrace._trace.span import Span

Trace = list[Span]

class ListStringTable(object):
    def index(self, string: str) -> int: ...

class BufferFull(Exception):
    pass

class BufferItemTooLarge(Exception):
    pass

class BufferedEncoder(object):
    content_type: str
    max_size: int
    max_item_size: int
    def __init__(self, max_size: int, max_item_size: int) -> None: ...
    def __len__(self) -> int: ...
    def put(self, item: Any) -> None: ...
    def encode(self) -> list[tuple[Optional[bytes], int]]: ...
    @property
    def size(self) -> int: ...

def packb(o: Any, **kwargs) -> bytes: ...
