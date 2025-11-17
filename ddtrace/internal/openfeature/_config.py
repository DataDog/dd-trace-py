from typing import Optional

from ddtrace.internal.native._native import ffe


FFE_CONFIG: Optional[ffe.Configuration] = None


def _get_ffe_config():
    """Retrieve the current FFE configuration."""
    return FFE_CONFIG


def _set_ffe_config(config):
    """Set the FFE configuration."""
    global FFE_CONFIG
    FFE_CONFIG = config
