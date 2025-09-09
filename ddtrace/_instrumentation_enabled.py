import os
from typing import Optional


TRUE_VALUES = {"1", "true", "yes", "on", "enable", "enabled"}
FALSE_VALUES = {"0", "false", "no", "off", "disable", "disabled"}


def getenv_bool(name: str) -> Optional[bool]:
    """Return (value, explicit). If unset/unknown → (default, False)."""
    raw = os.getenv(name)
    if raw is None:
        return None
    s = raw.strip().lower()
    if s in TRUE_VALUES:
        return True
    if s in FALSE_VALUES:
        return False
    return None


# Precedence: earlier wins. Second item = value that means "enabled" (and its default).
TOGGLES: dict[str, bool] = {
    "_DD_INSTRUMENTATION_DISABLED": False,  # enabled when value == False
    "DD_CIVISIBILITY_ENABLED": True,  # enabled when value == True
}


def resolve_instrumentation_enabled(global_default: bool = True) -> bool:
    for name, enabled_value in TOGGLES.items():
        val = getenv_bool(name)
        if val is not None:
            return val == enabled_value  # enabled iff env value equals its "enabled" value
    return global_default  # fallback (enabled by default)
