"""Select the HTTP client module OpenAI actually imports (httpx vs httpx2)."""

from types import ModuleType


def _http_client_module() -> ModuleType:
    """OpenAI 3.x depends on httpx2; 1.x still uses httpx.

    Import the chosen client lazily so collection succeeds when the other
    package is absent (OpenAI 3 environments ship httpx2 only).
    """
    openai_major: int = 0
    try:
        import openai as openai_mod

        openai_major = int(openai_mod.__version__.split(".", 1)[0])
    except Exception:
        pass
    if openai_major >= 3:
        try:
            import httpx2 as httpx2_mod
        except ImportError as err:
            raise ImportError("httpx2 is required for openai>=3") from err
        return httpx2_mod  # type: ignore[no-any-return]
    import httpx as httpx1

    return httpx1  # type: ignore[no-any-return]
