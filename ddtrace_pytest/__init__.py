"""
ddtrace_pytest - Proxy plugins for ddtrace pytest integration with killswitch support.

This package provides proxy plugins that conditionally load ddtrace pytest functionality
based on the DD_CIVISIBILITY_ENABLED environment variable, allowing for a complete
killswitch mechanism that avoids importing any ddtrace modules when disabled.
"""

__version__ = "1.0.0"
__all__ = []