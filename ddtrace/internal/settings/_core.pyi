from enum import Enum
from typing import Dict
from typing import Optional
from typing import cast

from envier import Env

FLEET_CONFIG: Dict[str, str]
LOCAL_CONFIG: Dict[str, str]
FLEET_CONFIG_IDS: Dict[str, str]

class ValueSource(str, Enum):
    FLEET_STABLE_CONFIG = cast(str, ...)
    ENV_VAR = cast(str, ...)
    LOCAL_STABLE_CONFIG = cast(str, ...)
    CODE = cast(str, ...)
    DEFAULT = cast(str, ...)
    UNKNOWN = cast(str, ...)
    OTEL_ENV_VAR = cast(str, ...)

class DDConfig(Env):
    fleet_source: Dict[str, str]
    local_source: Dict[str, str]
    env_source: Dict[str, str]
    _value_source: Dict[str, str]
    config_id: Optional[str]

    def __init__(
        self,
        source: Optional[Dict[str, str]] = None,
        parent: Optional[Env] = None,
        dynamic: Optional[Dict[str, str]] = None,
    ) -> None: ...
    def value_source(self, env_name: str) -> str: ...
