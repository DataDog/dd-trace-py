import gevent
import gevent.pool as gpool
from typing import Any

GEVENT_VERSION: Any

class TracingMixin:
    def __init__(self, *args: Any, **kwargs: Any) -> None: ...

class TracedGreenlet(TracingMixin, gevent.Greenlet):
    def __init__(self, *args: Any, **kwargs: Any) -> None: ...

class TracedIMapUnordered(TracingMixin, gpool.IMapUnordered):
    def __init__(self, *args: Any, **kwargs: Any) -> None: ...
