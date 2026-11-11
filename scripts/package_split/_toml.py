"""TOML loading for the harness, independent of the interpreter version.

Python 3.11+ ships tomllib; older interpreters use the tomli backport (a
declared dependency of the wrapper and the test suite). Importing from this
module everywhere avoids a scattered try/except in every gate.
"""

from __future__ import annotations


try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - depends on interpreter
    import tomli as tomllib

__all__ = ["tomllib"]
