from typing import Any
from typing import Mapping
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from ddtrace import Span


class BaseProtection(object):
    def process(self, span, data):
        # type: (Span, Mapping[str, Any]) -> None
        raise NotImplementedError
