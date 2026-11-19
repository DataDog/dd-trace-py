"""AppSec hooks for the requests integration.

Loaded through the ddtrace.integration_hooks entry point when the integration is patched.
"""

from wrapt import wrap_function_wrapper as _w

from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.utils.wrappers import unwrap as _u


_wrapped = False


def listen() -> None:
    global _wrapped

    # RASP needs Session.request because Session.send is too late to see the requested URL.
    if _wrapped or not asm_config._load_modules:
        return

    # Imported here so that patching requests doesn't load AppSec when it cannot be enabled.
    import requests

    from ddtrace.appsec._common_module_patches import wrapped_request_D8CB81E472AF98A2

    _w(requests, "Session.request", wrapped_request_D8CB81E472AF98A2)
    _wrapped = True


def unlisten() -> None:
    global _wrapped

    if not _wrapped:
        return

    import requests

    _u(requests.Session, "request")
    _wrapped = False
