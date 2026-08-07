"""
Module-level storage for the parsed FFE configuration and its consent value.

The two values are bundled in one NamedTuple with a single module-level
reference so a reader always sees a consistent (config, consent) pair. This
closes the consent-lifecycle race described in the Java pilot's review
(concern:bind-consent-to-evaluated-config).
"""

from typing import NamedTuple
from typing import Optional
from typing import Union

from ddtrace.internal.native._native import ffe


class _FfeSnapshot(NamedTuple):
    """Atomic bundle of native config and the consent value read off the UFC."""

    config: ffe.Configuration
    observe_full_evaluation_data: bool


# Module-level global. Reads and writes are done through the accessors below so
# callers only ever observe a consistent snapshot.
_FFE_SNAPSHOT: Optional[_FfeSnapshot] = None


# AIDEV-NOTE: Preserved for existing callers only. New callers must use
# _get_ffe_snapshot() so consent is observed atomically with the config --
# grabbing just the config bypasses the atomic snapshot and reintroduces
# the consent-lifecycle race the Java pilot hit (concern:bind-consent-to-
# evaluated-config). See docs/superpowers/specs/2026-08-06-pii-
# flagevaluations-hashing-design.md.
def _get_ffe_config() -> Optional[ffe.Configuration]:
    """Retrieve just the native FFE configuration. Preserved for compatibility."""
    snap = _FFE_SNAPSHOT
    return snap.config if snap is not None else None


def _get_ffe_snapshot() -> Optional[_FfeSnapshot]:
    """Retrieve the full snapshot (config + consent)."""
    return _FFE_SNAPSHOT


def _set_ffe_config(value: Union[None, ffe.Configuration, _FfeSnapshot]) -> None:
    """Set the FFE snapshot.

    Accepts either a bare native Configuration (existing test callers) or a
    _FfeSnapshot. A bare Configuration is stored as consent-off; None clears.
    """
    global _FFE_SNAPSHOT
    if value is None:
        _FFE_SNAPSHOT = None
    elif isinstance(value, _FfeSnapshot):
        _FFE_SNAPSHOT = value
    else:
        # Legacy path: a raw Configuration means consent-off (fail closed).
        _FFE_SNAPSHOT = _FfeSnapshot(config=value, observe_full_evaluation_data=False)
