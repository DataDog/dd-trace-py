from typing import Any
from typing import List
from typing import Union

from ddtrace.span import Span

class MsgpackEncoder(object):
    content_type: str
    def _decode(self, data: Union[str, bytes]) -> Any: ...
    def encode_traces(self, trace: List[List[Any]]) -> bytes: ...
    def trace_size(self, trace: List[Span]) -> int: ...
