import dataclasses
from time import monotonic
from typing import Sequence


@dataclasses.dataclass(frozen=True)
class HttpEndPoint:
    method: str
    path: str
    resource_name: str = dataclasses.field(default="")
    operation_name: str = dataclasses.field(default="http.request")
    response_body_type: Sequence[str] = dataclasses.field(default_factory=tuple)
    response_code: Sequence[int] = dataclasses.field(default_factory=tuple)
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

        # Forward just this newly-registered endpoint. The native worker dedupes and buffers
        # endpoints itself, so this avoids re-walking the whole collection on every call. This
        # no-ops until the worker exists; ``enable()`` replays the set once it does. The import
        # is deferred because the telemetry writer imports this module's ``endpoint_collection``.
        from ddtrace.internal.telemetry import telemetry_writer

        telemetry_writer._record_endpoint(
            endpoint.method, endpoint.path, endpoint.resource_name, endpoint.operation_name
        )


endpoint_collection = HttpEndPointsCollection()
