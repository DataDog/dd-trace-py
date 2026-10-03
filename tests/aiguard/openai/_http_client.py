"""Select the HTTP client module OpenAI actually imports (httpx vs httpx2)."""

from types import ModuleType

import httpx as httpx1


try:
    import httpx2 as httpx2_mod
except ImportError:
    httpx2_mod = None


def _http_client_module() -> ModuleType:
    """OpenAI 3.x depends on ``httpx2``; 1.x still uses ``httpx``."""
    openai_major: int = 0
    try:
        import openai as openai_mod

        openai_major = int(openai_mod.__version__.split(".", 1)[0])
    except Exception:
        pass
    if openai_major >= 3:
        if httpx2_mod is None:
            raise ImportError("httpx2 is required for openai>=3")
        return httpx2_mod  # type: ignore[no-any-return]
    return httpx1  # type: ignore[no-any-return]
