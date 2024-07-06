"""
Trace the standard library ``urllib.request`` library to trace
HTTP requests and detect SSRF vulnerabilities. It is enabled by default
if ``DD_IAST_ENABLED`` is set to ``True`` (for detecting sink points) and/or
``DD_ASM_ENABLED`` is set to ``True`` (for exploit prevention).
"""
from .patch import get_version
from .patch import patch
from .patch import unpatch


__all__ = ["patch", "unpatch", "get_version"]
