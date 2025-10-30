import contextvars
from typing import Optional


FFE_CONFIG: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar("ffe_config", default=None)


def _get_ffe_config():
    """Retrieve the current IAST context identifier from the ContextVar."""
    return FFE_CONFIG.get()


def _set_ffe_config(data):
    """Retrieve the current IAST context identifier from the ContextVar."""
    return FFE_CONFIG.set(data)
