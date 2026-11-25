"""Configuration objects telemetry reports when it is imported."""

from typing import Any
from typing import Callable
from typing import List
from typing import Tuple


# IMPORTANT: Do not change typing.List and typing.Tuple to list and tuple until minimum Python version is 3.11+
# Module-level list[...] in Python 3.10 affects import timing. See packages.py for details.
_configurations: List[Callable[[], Any]] = []  # noqa: UP006


def register_configuration(get_config: Callable[[], Any]) -> None:
    """Add a configuration object for telemetry to report when it is imported, before its own configurations.

    ``get_config`` returns a ``DDConfig`` and is called once, when telemetry is imported, so it can import the
    configuration lazily. Nothing is reported by default.
    """
    _configurations.append(get_config)


def get_configurations() -> Tuple[Callable[[], Any], ...]:  # noqa: UP006
    """Return the registered configuration getters, in registration order."""
    return tuple(_configurations)
