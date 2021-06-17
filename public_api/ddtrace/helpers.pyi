import Any
from ddtrace.tracer import Tracer
from typing import Optional, Tuple

def get_correlation_ids(tracer: Optional[Tracer]=...) -> Tuple[Optional[int], Optional[int]]: ...
