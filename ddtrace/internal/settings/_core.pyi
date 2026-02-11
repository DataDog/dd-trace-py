from enum import Enum
from typing import Dict
from typing import Optional
from typing import cast

from envier import Env

FLEET_CONFIG: Dict[str, str]  # noqa: UP006
LOCAL_CONFIG: Dict[str, str]  # noqa: UP006
FLEET_CONFIG_IDS: Dict[str, str]  # noqa: UP006

class ValueSource(str, Enum):
    FLEET_STABLE_CONFIG = cast(str, ...)
    ENV_VAR = cast(str, ...)
    LOCAL_STABLE_CONFIG = cast(str, ...)
    CODE = cast(str, ...)
    DEFAULT = cast(str, ...)
    UNKNOWN = cast(str, ...)
    OTEL_ENV_VAR = cast(str, ...)

class DDConfig(Env):
    fleet_source: Dict[str, str]  # noqa: UP006
    local_source: Dict[str, str]  # noqa: UP006
    env_source: Dict[str, str]  # noqa: UP006
    _value_source: Dict[str, str]  # noqa: UP006
    config_id: Optional[str]

    def __init__(
        self,
        source: Optional[Dict[str, str]] = None,  # noqa: UP006
        parent: Optional[Env] = None,
        dynamic: Optional[Dict[str, str]] = None,  # noqa: UP006
    ) -> None: ...
    def value_source(self, env_name: str) -> str: ...
