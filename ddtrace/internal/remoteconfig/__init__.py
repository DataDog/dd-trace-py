import abc
import dataclasses
from typing import Any
from typing import Dict
from typing import Optional
from typing import Sequence


@dataclasses.dataclass(unsafe_hash=True)
class ConfigMetadata:
    """
    Configuration TUF target metadata
    """

    id: str
    product_name: str
    sha256_hash: Optional[str]
    length: Optional[int]
    tuf_version: Optional[int]
    apply_state: Optional[int] = dataclasses.field(default=1, compare=False)
    apply_error: Optional[str] = dataclasses.field(default=None, compare=False)


# None means the configuration is deleted
PayloadType = Optional[Dict[str, Any]]


@dataclasses.dataclass
class Payload:
    metadata: ConfigMetadata
    path: str
    content: PayloadType

    def __post_init__(self):
        if isinstance(self.metadata, dict):
            self.metadata = ConfigMetadata(**self.metadata)


class RCCallback(abc.ABC):
    """Base class for remote config callbacks.

    All remote config product callbacks should inherit from this class.
    Callbacks receive a sequence of payloads and process them accordingly.

    Subclasses can optionally override the periodic() method to perform
    operations at every polling interval.
    """

    @abc.abstractmethod
    def __call__(self, payloads: Sequence[Payload]) -> None:
        """Process remote config payloads.

        Args:
            payloads: Sequence of configuration payloads to process
        """
        ...

    def periodic(self) -> None:
        """Method called at every polling operation.

        This method is invoked at every polling interval, even when there are
        no payloads. Callbacks can override this to perform periodic maintenance
        tasks, state checks, or time-based operations.

        The default implementation does nothing.
        """
        pass
