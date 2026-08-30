from __future__ import annotations

import logging
from typing import Callable
import warnings

from ddtrace.contrib.internal.logging.patch import get_version


def test_get_version_returns_string_without_deprecation_warning() -> None:
    """get_version() must return a string and not leak logging.__version__ DeprecationWarning."""

    def _deprecated_version(name: str) -> str:
        if name == "__version__":
            warnings.warn("logging.__version__ is deprecated", DeprecationWarning, stacklevel=2)
            return "1.2.3"
        raise AttributeError(name)

    saved_version: object | None = logging.__dict__.get("__version__")
    saved_getattr: Callable[[str], object] | None = logging.__dict__.get("__getattr__")
    try:
        logging.__dict__.pop("__version__", None)
        logging.__dict__["__getattr__"] = _deprecated_version
        caught: list[warnings.WarningMessage]
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", DeprecationWarning)
            version: str = get_version()
        assert isinstance(version, str)
        assert version == "1.2.3"
        leaked: list[warnings.WarningMessage] = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert leaked == []
    finally:
        if saved_getattr is not None:
            logging.__dict__["__getattr__"] = saved_getattr
        else:
            logging.__dict__.pop("__getattr__", None)
        if saved_version is not None:
            logging.__dict__["__version__"] = saved_version
        else:
            logging.__dict__.pop("__version__", None)
