"""Centralized environment variable access for dd-trace-py.

This module provides a drop-in replacement for os.environ, enabling centralized
control and future validation of all environment variable access in ddtrace.
"""

from collections.abc import Mapping
import os
from typing import Any
from typing import Iterator


class EnvConfig(Mapping):
    """A read-only Mapping wrapper around os.environ.

    Serves as the centralized entry point for all environment variable access
    in dd-trace-py. Inherits get, items, keys, values, etc. from Mapping.
    For writes, use os.environ directly or the setenv() helper.
    """

    def __getitem__(self, key: str) -> str:
        return os.environ[key]

    def __iter__(self) -> Iterator[str]:
        return iter(os.environ)

    def __len__(self) -> int:
        return len(os.environ)


dd_environ = EnvConfig()


def getenv(env_name: str, default: Any = None) -> Any:
    """Wrapper around dd_environ.get — use instead of os.getenv."""
    return dd_environ.get(env_name, default)


def setenv(env_name: str, value: Any) -> None:
    """Wrapper around os.environ assignment — use instead of os.environ[key] = value."""
    os.environ[env_name] = value


def unsetenv(env_name: str) -> None:
    """Wrapper around del os.environ[key] — use instead of os.unsetenv.

    Uses pop() so that missing keys are a no-op, matching os.unsetenv semantics.
    """
    os.environ.pop(env_name, None)
