from typing import Any
from typing import List
from typing import Union

class MsgpackEncoder(object):
    content_type: str
    def _decode(self, data: Union[str, bytes]) -> Any: ...
    def encode_traces(self, trace: List[List[Any]]) -> bytes: ...
