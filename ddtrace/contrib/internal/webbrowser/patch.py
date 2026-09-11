import webbrowser


def get_version() -> str:
    return ""


def _supported_versions() -> dict[str, str]:
    return {"webbrowser": "*"}


def patch():
    """patch the built-in webbrowser methods for tracing"""
    if getattr(webbrowser, "__datadog_patch", False):
        return
    webbrowser.__datadog_patch = True


def unpatch():
    """unpatch any previously patched modules"""
    if not getattr(webbrowser, "__datadog_patch", False):
        return
    webbrowser.__datadog_patch = False
