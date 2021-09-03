import abc
from typing import Any
from typing import Iterable
from typing import Mapping
from typing import TYPE_CHECKING

import six


if TYPE_CHECKING:
    from ddtrace import Span

from ddtrace.appsec.internal.events import Event


class BaseProtection(six.with_metaclass(abc.ABCMeta)):
    @abc.abstractmethod
    def process(self, span, data):
        # type: (Span, Mapping[str, Any]) -> Iterable[Event]
        raise NotImplementedError
