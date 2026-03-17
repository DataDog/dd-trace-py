"""Centralized environment variable access for dd-trace-py.

This module provides a wrapper around os.environ as part of the Configuration Registry
initiative, enabling centralized validation and control of environment variable access.
"""

from collections.abc import MutableMapping
import os
from typing import Any
from typing import Iterator


class EnvConfig(MutableMapping):
    """Wraps os.environ as a MutableMapping for the Configuration Registry initiative.

    This class serves as the centralized entry point for all environment variable access
    in dd-trace-py. Future validation against a list of allowed variables will be added
    to the read methods.
    """

    @property
    def _env(self) -> os._Environ:
        return os.environ

    def __getitem__(self, key: str) -> str:
        # TODO: Check will go here to validate the variable against list of allowed variables
        return self._env[key]

    def __setitem__(self, key: str, value: str) -> None:
        self._env[key] = value

    def __delitem__(self, key: str) -> None:
        del self._env[key]

    def __contains__(self, key: object) -> bool:
        # TODO: Check will go here to validate the variable against list of allowed variables
        return key in self._env

    def __iter__(self) -> Iterator[str]:
        # TODO: Check will go here to validate the variable against list of allowed variables
        return iter(self._env)

    def __len__(self) -> int:
        return len(self._env)

    def get(self, key: str, default: Any = None) -> Any:
        # TODO: Check will go here to validate the variable against list of allowed variables
        return self._env.get(key, default)

    def items(self):
        # TODO: Check will go here to validate the variable against list of allowed variables
        return self._env.items()

    def keys(self):
        # TODO: Check will go here to validate the variable against list of allowed variables
        return self._env.keys()

    def values(self):
        # TODO: Check will go here to validate the variable against list of allowed variables
        return self._env.values()


environ = EnvConfig()


def getenv(env_name: str, default: Any = None) -> Any:
    """Wrapper around environ.get — use instead of os.getenv."""
    return environ.get(env_name, default)


def set_env(env_name: str, value: Any) -> None:
    """Wrapper around os.environ assignment."""
    os.environ[env_name] = value
