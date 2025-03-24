import dataclasses
from typing import Any
from typing import Dict
from typing import Optional


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
