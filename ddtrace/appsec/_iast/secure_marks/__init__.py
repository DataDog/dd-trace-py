"""Package for IAST secure marks functionality.

This package provides functions to mark values as secure from specific vulnerabilities.
It includes both sanitizers (which transform and secure values) and validators (which
verify values are secure).

It also provides configuration capabilities for custom security controls via the
DD_IAST_SECURITY_CONTROLS_CONFIGURATION environment variable.
"""

from .configuration import VULNERABILITY_TYPE_MAPPING
from .configuration import SecurityControl
from .configuration import get_security_controls_from_env
from .configuration import parse_security_controls_config
from .sanitizers import cmdi_sanitizer
from .sanitizers import path_traversal_sanitizer
from .sanitizers import sqli_sanitizer
from .validators import cmdi_validator
from .validators import path_traversal_validator
from .validators import sqli_validator


__all__ = [
    # Sanitizers
    "path_traversal_sanitizer",
    "sqli_sanitizer",
    "cmdi_sanitizer",
    # Validators
    "path_traversal_validator",
    "sqli_validator",
    "cmdi_validator",
    # Configuration
    "get_security_controls_from_env",
    "parse_security_controls_config",
    "SecurityControl",
    "VULNERABILITY_TYPE_MAPPING",
]
