"""AI Guard third-party integration submodules.

This package is intentionally lightweight: importing it must not pull in any
optional third-party SDK. In particular the Strands classes live in the
``.strands`` submodule and are loaded lazily by ``ddtrace.aiguard``'s
module-level ``__getattr__`` on first attribute access — never at
``ddtrace.aiguard`` import time. See the AIDEV-NOTE in the parent package's
``__init__.py`` for the regression this protects against.
"""
