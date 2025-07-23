import dataclasses
from typing import List


@dataclasses.dataclass(frozen=True)
class HttpEndPoint:
    method: str
    path: str
    operation_name: str = dataclasses.field(default="http.request", init=False)
    resource_name: str = dataclasses.field(init=False)

    def __post_init__(self):
        super().__setattr__("method", self.method.upper())
        super().__setattr__("resource_name", f"{self.method} {self.path}")


@dataclasses.dataclass()
class HttpEndPointsCollection:
    endpoints: List[HttpEndPoint] = dataclasses.field(default_factory=list, init=False)
    is_first: bool = dataclasses.field(default=True, init=False)

    def reset(self):
        """Reset the collection to its initial state."""
        self.endpoints.clear()
        self.is_first = True
