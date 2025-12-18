from typing import Optional


# https://cloud.google.com/vertex-ai/generative-ai/docs/partner-models/use-partner-models
# GeminiAPI: only exports google provided models
# VertexAI: can map provided models to provider based on prefix, a best effort mapping
# as huggingface exports hundreds of custom provided models
KNOWN_MODEL_PREFIX_TO_PROVIDER = {
    "gemini": "google",
    "imagen": "google",
    "veo": "google",
    "text-embedding": "google",
    "jamba": "ai21",
    "claude": "anthropic",
    "llama": "meta",
    "mistral": "mistral",
    "codestral": "mistral",
    "deepseek": "deepseek",
    "olmo": "ai2",
    "tulu": "ai2",
    "molmo": "ai2",
    "specter": "ai2",
    "cosmoo": "ai2",
    "qodo": "qodo",
    "mars": "camb.ai",
}


def normalize_model_name(model_path: str) -> str:
    """
    Strip path prefixes from model names.

    Examples:
        >>> normalize_model_name("models/gemini-2.0-flash")
        "gemini-2.0-flash"
        >>> normalize_model_name("gemini-2.0-flash")
        "gemini-2.0-flash"

    Args:
        model_path: The full model path or name

    Returns:
        The normalized model name with path prefix stripped
    """
    if not model_path:
        return model_path
    return model_path.split("/")[-1] if "/" in model_path else model_path


def get_provider_from_model_name(model_name: str) -> Optional[str]:
    """
    Determine provider from model name prefix.

    Args:
        model_name: The model name to check

    Returns:
        The provider name if matched, None otherwise
    """
    if not model_name:
        return None
    model_lower = model_name.lower()
    for prefix, provider in KNOWN_MODEL_PREFIX_TO_PROVIDER.items():
        if model_lower.startswith(prefix):
            return provider
    return None

