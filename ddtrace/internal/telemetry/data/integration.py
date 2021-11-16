from typing import TypedDict


Integration = TypedDict(
    "Integration",
    {"name": str, "version": str, "enabled": bool, "auto_enabled": bool, "compatible": str, "error": str},
)
"""
Stores information about modules we attempt to instrument
"""


def create_integration(name, version="", enabled=True, auto_enabled=True, compatible="", error=""):
    # type: (str, str, bool, bool, str, str) -> Integration
    """creates a default integration object"""
    return {
        "name": name,
        "version": version,
        "enabled": enabled,
        "auto_enabled": auto_enabled,
        "compatible": compatible,
        "error": error,
    }
