"""Centralized environment variable access for dd-trace-py.

This module provides a drop-in replacement for os.environ, enabling centralized
control and future validation of all environment variable access in ddtrace.
"""

from collections.abc import MutableMapping
import os
from typing import Iterator


class EnvConfig(MutableMapping):
    """A MutableMapping wrapper around os.environ.

    Serves as the centralized entry point for all environment variable access
    in dd-trace-py. Drop-in replacement for os.environ — supports reads, writes,
    deletes, containment checks, iteration, and all standard dict-like operations.
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

    def copy(self) -> dict:
        return dict(self)


dd_environ = EnvConfig()
