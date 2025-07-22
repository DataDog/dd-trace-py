from dataclasses import dataclass


@dataclass
class DeferredSpan:
    resource: str
    start_ns: int
    end_ns: int


DeferredSpans = list[DeferredSpan]()
