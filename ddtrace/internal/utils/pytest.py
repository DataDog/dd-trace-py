"""Pytest integration utilities."""
import os
import sys
from typing import Optional


def is_pytest_plugin_enabled() -> bool:
    """Check if the ddtrace pytest plugin is enabled.
    
    Returns:
        bool: True if pytest is running with the ddtrace plugin enabled,
              False otherwise or if not in a pytest context.
    """
    # Check if we're running under pytest
    if "pytest" not in sys.modules:
        return False
    
    try:
        import pytest
        from ddtrace.contrib.internal.pytest.plugin import is_enabled
        
        # Check if we can get the current pytest config
        config = getattr(pytest, "config", None)
        if config is None:
            # If we can't get the config, check environment variables as a fallback
            return os.environ.get("DD_PYTEST_PLUGIN_ENABLED", "").lower() in ("1", "true", "yes")
            
        return is_enabled(config)
    except (ImportError, AttributeError):
        return False
