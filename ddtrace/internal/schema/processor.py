from typing import Optional
from typing import Protocol
from typing import TypeVar
from typing import cast

from ddtrace._trace.processor import TraceProcessor
from ddtrace.constants import _BASE_SERVICE_KEY
from ddtrace.internal.settings._config import config

from . import schematize_service_name


class _ServiceSpan(Protocol):
    service: Optional[str]

    def _set_attribute(self, key: str, value: str) -> None: ...


_ServiceSpanT = TypeVar("_ServiceSpanT", bound=_ServiceSpan)


class BaseServiceProcessor(TraceProcessor):
    def __init__(self) -> None:
        self._global_service = schematize_service_name((config.service or "").lower())

    def process_trace(self, trace: list[_ServiceSpanT]) -> list[_ServiceSpanT]:
        if not trace:
            return trace

        traces_to_process = filter(
            lambda x: x.service and x.service.lower() != self._global_service,
            trace,
        )
        any(map(lambda x: self._update_dd_base_service(x), traces_to_process))

        return trace

    def _update_dd_base_service(self, span: _ServiceSpan) -> None:
        span._set_attribute(key=_BASE_SERVICE_KEY, value=cast(str, self._global_service))
