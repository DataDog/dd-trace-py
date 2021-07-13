from typing import Any
from typing import List
from typing import Optional
from typing import Union

from ddtrace.span import Span

Trace = List[Span]

class BufferFull(Exception):
    pass

class BufferItemTooLarge(Exception):
    pass

class BufferedEncoder(object):
    content_type: str
    def __init__(self, max_size: int, max_item_size: int) -> None: ...
    def __len__(self) -> int: ...
    def put(self, trace: Trace) -> None: ...
    def encode(self) -> Optional[bytes]: ...
    @property
    def size(self) -> int: ...
    @property
    def max_size(self) -> int: ...
    @property
    def max_item_size(self) -> int: ...

class MsgpackEncoder(BufferedEncoder):
    def _decode(self, data: Union[str, bytes]) -> Any: ...
