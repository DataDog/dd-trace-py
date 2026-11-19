import urllib.request


def get_version() -> str:
    return ""


def _supported_versions() -> dict[str, str]:
    return {"urllib": "*"}


def patch() -> None:
    """patch the built-in urllib.request methods for tracing"""
    if getattr(urllib.request, "__datadog_patch", False):
        return
    setattr(urllib.request, "__datadog_patch", True)


def unpatch() -> None:
    """unpatch any previously patched modules"""
    if not getattr(urllib.request, "__datadog_patch", False):
        return
    setattr(urllib.request, "__datadog_patch", False)
