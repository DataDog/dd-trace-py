"""AppSec hooks for the httplib integration.

Loaded through the ddtrace.integration_hooks entry point when the integration is patched.
"""

# The alias form resolves through sys.modules: the integration is patched while http.client is still
# being imported, before it is bound as an attribute of the http package.
import http.client as httplib
from typing import Any
from typing import Callable

from wrapt import wrap_function_wrapper as _w

from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.utils.wrappers import unwrap as _u


_wrapped = False


def _wrap_request(
    wrapped: Callable[..., Any], instance: httplib.HTTPConnection, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    # Checked per call: remote configuration can enable AppSec after the integration is patched.
    if asm_config._asm_enabled and asm_config._ep_enabled:
        # Imported here so that patching httplib doesn't load AppSec while it is disabled.
        from ddtrace.appsec._common_module_patches import wrapped_http_client_request

        return wrapped_http_client_request(wrapped, instance, args, kwargs)
    return wrapped(*args, **kwargs)


def listen() -> None:
    global _wrapped

    if _wrapped:
        return
    _w(httplib, "HTTPConnection.request", _wrap_request)
    _wrapped = True


def unlisten() -> None:
    global _wrapped

    # The integration unpatches after this, so the outermost wrapper must be ours.
    if not _wrapped:
        return
    _u(httplib.HTTPConnection, "request")
    _wrapped = False
