import Any

class RuntimeMetrics:
    @staticmethod
    def enable(tracer: Any=..., dogstatsd_url: Any=..., flush_interval: Any=...) -> None: ...
    @staticmethod
    def disable() -> None: ...
