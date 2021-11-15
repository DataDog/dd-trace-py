from typing import TypedDict


Integration = TypedDict(
    "Integration",
    {"name": str, "version": str, "enabled": bool, "auto_enabled": bool, "compatible": str, "errors": str},
)


def create_integration(name, version="", enabled=True, auto_enabled=True, compatible="", errors=""):
    # type: (str, str, bool, bool, str, str) -> Integration
    return {
        "name": name,
        "version": version,
        "enabled": enabled,
        "auto_enabled": auto_enabled,
        "compatible": compatible,
        "errors": errors,
    }


def on_exception(integration, exception, compatible="no"):
    # type: (Integration, str, str) -> None
    integration["errors"] = exception
    integration["compatible"] = compatible
    integration["enabled"] = False
