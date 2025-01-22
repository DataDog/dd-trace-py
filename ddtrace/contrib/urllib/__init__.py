"""
Trace the standard library ``urllib.request`` library to trace
HTTP requests and detect SSRF vulnerabilities. It is enabled by default
if ``DD_IAST_ENABLED`` is set to ``True`` (for detecting sink points) and/or
``DD_ASM_ENABLED`` is set to ``True`` (for exploit prevention).
"""


# Required to allow users to import from  `ddtrace.contrib.urllib.patch` directly
import warnings as _w


with _w.catch_warnings():
    _w.simplefilter("ignore", DeprecationWarning)
    from . import patch as _  # noqa: F401, I001


from ddtrace.contrib.internal.urllib.patch import get_version  # noqa: F401
from ddtrace.contrib.internal.urllib.patch import patch  # noqa: F401
from ddtrace.contrib.internal.urllib.patch import unpatch  # noqa: F401
