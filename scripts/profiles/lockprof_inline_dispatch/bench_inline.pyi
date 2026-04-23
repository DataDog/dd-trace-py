from typing import Any

class Lock:
    __wrapped__: Any
    capture_sampler: Any
    acquired_time: Any
    is_internal: bool

    def __init__(self, wrapped: Any, sampler: Any) -> None: ...
    def acquire(self, *args: Any, **kwargs: Any) -> Any: ...
    def release(self, *args: Any, **kwargs: Any) -> Any: ...
