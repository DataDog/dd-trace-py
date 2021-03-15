from typing import Any
from typing import Union

class MsgpackEncoder(object):
    content_type: str
    def _decode(self, data: Union[str, bytes]) -> Any: ...
