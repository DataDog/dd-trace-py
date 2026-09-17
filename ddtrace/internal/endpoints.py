import dataclasses
from time import monotonic
from typing import Callable
from typing import Optional
from typing import Sequence


@dataclasses.dataclass(frozen=True)
class HttpEndPoint:
    method: str
    path: str
    resource_name: str = dataclasses.field(default="")
    operation_name: str = dataclasses.field(default="http.request")
    # compare=False so equality matches __hash__ (method, path): a frozen dataclass otherwise
    # compares every field, and the same route registered with different reported metadata would
    # be a distinct set member - stored twice and forwarded to the worker twice, leaving the
    # dedupe this set exists for to native code.
    response_body_type: Sequence[str] = dataclasses.field(default_factory=tuple, compare=False)
    response_code: Sequence[int] = dataclasses.field(default_factory=tuple, compare=False)
    _hash: int = dataclasses.field(init=False, repr=False)

    def __post_init__(self) -> None:
        super().__setattr__("method", self.method.upper())
        if not self.resource_name:
            super().__setattr__("resource_name", f"{self.method} {self.path}")
        # cache hash result
        super().__setattr__("_hash", hash((self.method, self.path)))

    def __hash__(self) -> int:
        return self._hash


class Singleton(type):
    """Singleton Class."""

    _instances: dict[type, object] = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(Singleton, cls).__call__(*args, **kwargs)
        return cls._instances[cls]


@dataclasses.dataclass()
class HttpEndPointsCollection(metaclass=Singleton):
    """Tracks the HTTP endpoints registered for ASM API-security telemetry.

    Endpoints are forwarded to the native telemetry worker as they are registered; the worker
    owns their deduplication, buffering, and emission of the ``app-endpoints`` payload. This
    set is kept only to:

    - deduplicate, so each endpoint is forwarded to the worker at most once,
    - bound how many endpoints are tracked (``max_size_length``),
    - drop a stale route table after a long idle gap (e.g. a dev-server hot reload), and
    - replay endpoints registered before the worker existed (see
      ``TelemetryWriter._report_endpoints``, called from ``enable()``).
    """

    endpoints: set[HttpEndPoint] = dataclasses.field(default_factory=set, init=False)
    drop_time_seconds: float = dataclasses.field(default=90.0, init=False)
    last_modification_time: float = dataclasses.field(default_factory=monotonic, init=False)
    max_size_length: int = dataclasses.field(default=900, init=False)
    # Notified of each newly-registered endpoint. The telemetry writer installs itself here in
    # ``enable()``; nothing else subscribes. The dependency points that way round so that this
    # module, which every web framework integration imports, needs no import of the telemetry
    # package -- importing it here would close a cycle back through ``telemetry.writer``.
    on_endpoint_registered: Optional[Callable[[HttpEndPoint], None]] = dataclasses.field(default=None, init=False)

    def reset(self) -> None:
        """Reset the collection to its initial state."""
        self.endpoints.clear()
        self.last_modification_time = monotonic()

    def add_endpoint(
        self,
        method: str,
        path: str,
        resource_name: str = "",
        operation_name: str = "http.request",
        response_body_type: Sequence[str] = (),
        response_code: Sequence[int] = (),
    ) -> None:
        """Register an endpoint and forward it (once) to the native telemetry worker."""
        current_time = monotonic()
        # Drop the accumulated set after a long idle gap (e.g. a hot reload of the server) so a
        # fresh route table can be re-registered and re-reported.
        if current_time - self.last_modification_time > self.drop_time_seconds:
            self.reset()
        if len(self.endpoints) >= self.max_size_length:
            return

        endpoint = HttpEndPoint(
            method=method,
            path=path,
            resource_name=resource_name,
            operation_name=operation_name,
            response_body_type=response_body_type,
            response_code=response_code,
        )
        if endpoint in self.endpoints:
            # Already registered (and already forwarded): dedupe without re-forwarding.
            return
        self.endpoints.add(endpoint)
        self.last_modification_time = current_time

        # Forward just this newly-registered endpoint, so the subscriber never re-walks the whole
        # collection. Nobody is subscribed until telemetry is enabled, which replays the set.
        if self.on_endpoint_registered is not None:
            self.on_endpoint_registered(endpoint)


endpoint_collection = HttpEndPointsCollection()
