import Any
from .propagator import Propagator

log: Any
HTTP_BAGGAGE_PREFIX: str
HTTP_BAGGAGE_PREFIX_LEN: Any

class HTTPPropagator(Propagator):
    @staticmethod
    def inject(span_context: Any, carrier: Any) -> None: ...
    @staticmethod
    def extract(carrier: Any) -> Any: ...
