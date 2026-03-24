"""Centralized environment variable access for dd-trace-py.

This module provides a drop-in replacement for os.environ, enabling centralized
control and future validation of all environment variable access in ddtrace.
"""

from collections.abc import MutableMapping
import os
from typing import Any
from typing import Iterator


class EnvConfig(MutableMapping):
    """A MutableMapping wrapper around os.environ.

    Serves as the centralized entry point for all environment variable access
    in dd-trace-py. Implements the five core MutableMapping abstract methods and
    inherits the rest (get, items, keys, values, etc.) from MutableMapping.
    """

    def __getitem__(self, key: str) -> str:
        return os.environ[key]

    def __setitem__(self, key: str, value: str) -> None:
        os.environ[key] = value

    def __delitem__(self, key: str) -> None:
        del os.environ[key]

    def __iter__(self) -> Iterator[str]:
        return iter(os.environ)

    def __len__(self) -> int:
        return len(os.environ)


dd_environ = EnvConfig()


def getenv(env_name: str, default: Any = None) -> Any:
    """Wrapper around dd_environ.get — use instead of os.getenv."""
    return dd_environ.get(env_name, default)


def setenv(env_name: str, value: Any) -> None:
    """Wrapper around dd_environ assignment."""
    dd_environ[env_name] = value
