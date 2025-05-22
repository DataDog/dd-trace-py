from collections import ChainMap
from enum import Enum
import os
from typing import Dict  # noqa:F401
from typing import Optional  # noqa:F401
from typing import Union  # noqa:F401

from envier import Env

from ddtrace.internal.native import get_configuration_from_disk


FLEET_CONFIG, LOCAL_CONFIG, FLEET_CONFIG_IDS = get_configuration_from_disk()


class ValueSource(str, Enum):
    FLEET_STABLE_CONFIG = "fleet_stable_config"
    ENV_VAR = "env_var"
    LOCAL_STABLE_CONFIG = "local_stable_config"
    CODE = "code"
    DEFAULT = "default"
    UNKNOWN = "unknown"


class DDConfig(Env):
    """Provides support for loading configurations from multiple sources."""

    def __init__(
        self,
        source: Optional[Dict[str, str]] = None,
        parent: Optional["Env"] = None,
        dynamic: Optional[Dict[str, str]] = None,
    ) -> None:
        self.fleet_source = FLEET_CONFIG
        self.local_source = LOCAL_CONFIG
        self.env_source = os.environ

        # Order of precedence: provided source < local stable config < environment variables < fleet stable config
        full_source = ChainMap(self.fleet_source, self.env_source, self.local_source, source or {})

        # Parse the configuration and initialize the values
        super().__init__(source=full_source, parent=parent, dynamic=dynamic)

        # Initialize the value sources
        self._value_source = {}

        for name, e in type(self).items(recursive=True):
            if e.private:
                continue

            env_name = e.full_name

            # Get the item value recursively
            env_val = self
            for p in name.split("."):
                env_val = getattr(env_val, p)

            if env_name in self.fleet_source:
                value_source = ValueSource.FLEET_STABLE_CONFIG
            elif env_name in self.env_source:
                value_source = ValueSource.ENV_VAR
            elif env_name in self.local_source:
                value_source = ValueSource.LOCAL_STABLE_CONFIG
            elif env_name in self.source:
                value_source = ValueSource.CODE
            elif env_val == e.default:
                value_source = ValueSource.DEFAULT
            else:
                value_source = ValueSource.UNKNOWN

            self._value_source[env_name] = value_source

            if value_source == ValueSource.FLEET_STABLE_CONFIG:
                self.config_id = FLEET_CONFIG_IDS.get(env_name)
            else:
                self.config_id = None

    def value_source(self, env_name: str) -> ValueSource:
        return self._value_source.get(env_name, ValueSource.UNKNOWN)
