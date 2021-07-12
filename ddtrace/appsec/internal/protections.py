from typing import Any
from typing import Mapping


class BaseProtection(object):
    def process(self, context_id, data):
        # type: (int, Mapping[str, Any]) -> None
        raise NotImplementedError
