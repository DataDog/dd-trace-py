from typing import Mapping


FFE_CONFIG: Mapping = {}


def _get_ffe_config():
    """Retrieve the current IAST context identifier from the ContextVar."""
    return FFE_CONFIG


def _set_ffe_config(data):
    global FFE_CONFIG
    """Retrieve the current IAST context identifier from the ContextVar."""
    FFE_CONFIG = data
