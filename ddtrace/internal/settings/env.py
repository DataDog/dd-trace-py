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
    in dd-trace-py. It implements the five core MutableMapping methods and inherits the
    rest (get, items, keys, values, etc.) from MutableMapping.

    Next steps:
        - Add validation against a registry of allowed environment variables in the
          read methods (__getitem__, __contains__, __iter__) to detect and warn on
          unrecognized variable access.
    """

    @property
    def _env(self) -> os._Environ:
        return os.environ

    def __getitem__(self, key: str) -> str:
        return self._env[key]

    def __setitem__(self, key: str, value: str) -> None:
        self._env[key] = value

    def __delitem__(self, key: str) -> None:
        del self._env[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._env)

    def __len__(self) -> int:
        return len(self._env)


dd_environ = EnvConfig()


def getenv(env_name: str, default: Any = None) -> Any:
    """Wrapper around dd_environ.get — use instead of os.getenv."""
    return dd_environ.get(env_name, default)


def setenv(env_name: str, value: Any) -> None:
    """Wrapper around dd_environ assignment."""
    dd_environ[env_name] = value
