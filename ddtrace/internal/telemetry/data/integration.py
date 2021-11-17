from typing import TypedDict


# Stores information about modules we attempt to instrument
Integration = TypedDict(
    "Integration",
    {"name": str, "version": str, "enabled": bool, "auto_enabled": bool, "compatible": str, "error": str},
)


def create_integration(name, version="", enabled=True, auto_enabled=True, compatible="", error=""):
    # type: (str, str, bool, bool, str, str) -> Integration
    """creates an Integration Dict and sets default values"""
    return {
        "name": name,
        "version": version,
        "enabled": enabled,
        "auto_enabled": auto_enabled,
        "compatible": compatible,
        "error": error,
    }


def on_exception(integration, exception, compatible="no"):
    # type: (Integration, str, str) -> None
    integration["errors"] = exception
    integration["compatible"] = compatible
    integration["enabled"] = False
