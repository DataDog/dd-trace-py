"""
ddtrace_pytest - Simple proxy plugins for ddtrace pytest integration with killswitch support.

This package provides simple proxy plugins that use `import *` branching to conditionally
load ddtrace pytest functionality based on the DD_CIVISIBILITY_ENABLED environment variable.

When DD_CIVISIBILITY_ENABLED=true/1:  imports from actual ddtrace plugin modules, else expose
disabled functionality to avoid breaking (for example, for pytest options exposed by ddtrace)
"""

import os


# Read the environment variable once at package import time
_CIVISIBILITY_ENABLED = os.getenv("DD_CIVISIBILITY_ENABLED", "true").lower() in ("true", "1")

__version__ = "1.0.0"
__all__ = ["_CIVISIBILITY_ENABLED"]
