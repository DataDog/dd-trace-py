import dataclasses
from time import monotonic
from typing import List


@dataclasses.dataclass(frozen=True)
class HttpEndPoint:
    method: str
    path: str
    resource_name: str = dataclasses.field(default="")
    operation_name: str = dataclasses.field(default="http.request")

    def __post_init__(self) -> None:
        super().__setattr__("method", self.method.upper())
        if not self.resource_name:
            super().__setattr__("resource_name", f"{self.method} {self.path}")


@dataclasses.dataclass()
class HttpEndPointsCollection:
    """A collection of HTTP endpoints that can be modified and flushed to a telemetry payload.

    The collection collects HTTP endpoints at startup and can be flushed to a telemetry payload.
    It maintains a maximum size and drops endpoints after a certain time period in case of a hot reload of the server.
    """

    endpoints: List[HttpEndPoint] = dataclasses.field(default_factory=list, init=False)
    is_first: bool = dataclasses.field(default=True, init=False)
    drop_time_seconds: float = dataclasses.field(default=90.0, init=False)
    last_modification_time: float = dataclasses.field(default_factory=monotonic, init=False)
    max_size_length: int = dataclasses.field(default=900, init=False)

    def reset(self) -> None:
        """Reset the collection to its initial state."""
        self.endpoints.clear()
        self.is_first = True
        self.last_modification_time = monotonic()

    def add_endpoint(
        self, method: str, path: str, resource_name: str = "", operation_name: str = "http.request"
    ) -> None:
        """
        Add an endpoint to the collection.
        """
        current_time = monotonic()
        if current_time - self.last_modification_time > self.drop_time_seconds:
            self.reset()
            self.endpoints.append(
                HttpEndPoint(method=method, path=path, resource_name=resource_name, operation_name=operation_name)
            )
        elif len(self.endpoints) < self.max_size_length:
            self.last_modification_time = current_time
            self.endpoints.append(
                HttpEndPoint(method=method, path=path, resource_name=resource_name, operation_name=operation_name)
            )

    def flush(self, max_length: int) -> dict:
        """
        Flush the endpoints to a payload, returning the first `max` endpoints.
        """
        if max_length >= len(self.endpoints):
            res = {
                "is_first": self.is_first,
                "endpoints": [dataclasses.asdict(ep) for ep in self.endpoints],
            }
            self.reset()
            return res
        else:
            res = {
                "is_first": self.is_first,
                "endpoints": [dataclasses.asdict(ep) for ep in self.endpoints[:max_length]],
            }
            self.endpoints = self.endpoints[max_length:]
            self.is_first = False
            self.last_modification_time = monotonic()
            return res
