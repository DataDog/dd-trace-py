import Any
from typing import Optional

DEFAULT_SERVICE: str

def get_traced_redis(ddtracer: Any, service: Any=..., meta: Optional[Any] = ...) -> Any: ...
def get_traced_redis_from(ddtracer: Any, baseclass: Any, service: Any = ..., meta: Optional[Any] = ...) -> Any: ...
