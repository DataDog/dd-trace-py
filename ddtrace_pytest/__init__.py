"""
ddtrace_pytest - Simple proxy plugins for ddtrace pytest integration with killswitch support.

This package provides ultra-simple proxy plugins that use import * branching to conditionally
load ddtrace pytest functionality based on the DD_CIVISIBILITY_ENABLED environment variable.

When DD_CIVISIBILITY_ENABLED=false/0: imports from disabled_* modules (no-op implementations)
When DD_CIVISIBILITY_ENABLED=true/1:  imports from real ddtrace plugin modules
"""

import os


# Read the environment variable once at package import time
_CIVISIBILITY_ENABLED = os.getenv("DD_CIVISIBILITY_ENABLED", "true").lower() in ("true", "1")

__version__ = "1.0.0"
__all__ = ["_CIVISIBILITY_ENABLED"]
