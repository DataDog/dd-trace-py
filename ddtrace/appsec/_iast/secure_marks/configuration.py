"""Module for parsing and applying DD_IAST_SECURITY_CONTROLS_CONFIGURATION.

This module handles the configuration of custom security controls via environment variables.
It supports both INPUT_VALIDATOR and SANITIZER types with configurable vulnerability types,
modules, methods, and parameter positions.

Format: CONTROL_TYPE:VULNERABILITY_TYPES:MODULE:METHOD[:PARAMETER_POSITIONS]
Example: INPUT_VALIDATOR:COMMAND_INJECTION,XSS:shlex:quote
"""

from typing import List
from typing import Optional

from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)

# Mapping from string names to VulnerabilityType enum values
VULNERABILITY_TYPE_MAPPING = {
    "CODE_INJECTION": VulnerabilityType.CODE_INJECTION,
    "COMMAND_INJECTION": VulnerabilityType.COMMAND_INJECTION,
    "HEADER_INJECTION": VulnerabilityType.HEADER_INJECTION,
    "UNVALIDATED_REDIRECT": VulnerabilityType.UNVALIDATED_REDIRECT,
    "INSECURE_COOKIE": VulnerabilityType.INSECURE_COOKIE,
    "NO_HTTPONLY_COOKIE": VulnerabilityType.NO_HTTPONLY_COOKIE,
    "NO_SAMESITE_COOKIE": VulnerabilityType.NO_SAMESITE_COOKIE,
    "PATH_TRAVERSAL": VulnerabilityType.PATH_TRAVERSAL,
    "SQL_INJECTION": VulnerabilityType.SQL_INJECTION,
    "SQLI": VulnerabilityType.SQL_INJECTION,  # Alias
    "SSRF": VulnerabilityType.SSRF,
    "STACKTRACE_LEAK": VulnerabilityType.STACKTRACE_LEAK,
    "WEAK_CIPHER": VulnerabilityType.WEAK_CIPHER,
    "WEAK_HASH": VulnerabilityType.WEAK_HASH,
    "WEAK_RANDOMNESS": VulnerabilityType.WEAK_RANDOMNESS,
    "XSS": VulnerabilityType.XSS,
}

SC_SANITIZER = "SANITIZER"
SC_VALIDATOR = "INPUT_VALIDATOR"


class SecurityControl:
    """Represents a single security control configuration."""

    def __init__(
        self,
        control_type: str,
        vulnerability_types: List[VulnerabilityType],
        module_path: str,
        method_name: str,
        parameters: Optional[List[str]] = None,
    ):
        """Initialize a security control configuration.

        Args:
            control_type: Either SC_VALIDATOR or SC_SANITIZER
            vulnerability_types: List of vulnerability types this control applies to
            module_path: Python module path (e.g., "shlex", "django.utils.http")
            method_name: Name of the method to wrap
            parameters: Optional list of parameter types for overloaded methods
        """
        self.control_type = control_type.upper()
        self.vulnerability_types = vulnerability_types
        self.module_path = module_path
        self.method_name = method_name
        self.parameters = parameters or []

        if self.control_type not in (SC_VALIDATOR, SC_SANITIZER):
            raise ValueError(f"Invalid control type: {control_type}")

    def __repr__(self):
        return (
            f"SecurityControl(type={self.control_type}, "
            f"vulns={[v.name for v in self.vulnerability_types]}, "
            f"module={self.module_path}, method={self.method_name})"
        )


def parse_vulnerability_types(vuln_string: str) -> List[VulnerabilityType]:
    """Parse comma-separated vulnerability types or '*' for all types.

    Args:
        vuln_string: Comma-separated vulnerability type names or '*'

    Returns:
        List of VulnerabilityType enum values

    Raises:
        ValueError: If an unknown vulnerability type is specified
    """
    if vuln_string.strip() == "*":
        return list(VULNERABILITY_TYPE_MAPPING.values())

    vulnerability_types = []
    for vuln_name in vuln_string.split(","):
        vuln_name = vuln_name.strip().upper()
        if vuln_name not in VULNERABILITY_TYPE_MAPPING:
            raise ValueError(f"Unknown vulnerability type: {vuln_name}")
        vulnerability_types.append(VULNERABILITY_TYPE_MAPPING[vuln_name])

    return vulnerability_types


def parse_parameters(positions_string: str) -> List[int]:
    """Parse comma-separated parameter positions.

    Args:
        positions_string: Comma-separated parameter positions (e.g., "0,1,3")

    Returns:
        List of integer positions

    Raises:
        ValueError: If positions cannot be parsed as integers
    """
    if not positions_string.strip():
        return []

    try:
        return [int(pos.strip()) for pos in positions_string.split(",")]
    except ValueError as e:
        raise ValueError(f"Invalid parameter positions: {positions_string}") from e


def parse_security_controls_config(config_string: str) -> List[SecurityControl]:
    """Parse the DD_IAST_SECURITY_CONTROLS_CONFIGURATION environment variable.

    Args:
        config_string: Configuration string with format:
                      CONTROL_TYPE:VULNERABILITY_TYPES:MODULE:METHOD[:PARAMETERS][:PARAMETER_POSITIONS]

    Returns:
        List of SecurityControl objects

    Raises:
        ValueError: If the configuration format is invalid
    """
    if not config_string.strip():
        return []

    security_controls = []

    # Split by semicolon to get individual security controls
    for control_config in config_string.split(";"):
        control_config = control_config.strip()
        if not control_config:
            continue

        # Split by colon to get control fields
        fields = control_config.split(":")
        if len(fields) < 4:
            log.warning("Invalid security control configuration (missing fields): %s", control_config)
            continue

        try:
            control_type = fields[0].strip()
            vulnerability_types = parse_vulnerability_types(fields[1].strip())
            module_path = fields[2].strip()
            method_name = fields[3].strip()

            # Optional fields
            parameters = None

            if len(fields) > 4 and fields[4].strip():
                parameters = parse_parameters(fields[4])

            security_control = SecurityControl(
                control_type=control_type,
                vulnerability_types=vulnerability_types,
                module_path=module_path,
                method_name=method_name,
                parameters=parameters,
            )

            security_controls.append(security_control)
            log.debug("Parsed security control: %s", security_control)

        except Exception:
            log.warning("Failed to parse security control %s", control_config, exc_info=True)
            continue

    return security_controls


def get_security_controls_from_env() -> List[SecurityControl]:
    """Get security controls configuration from DD_IAST_SECURITY_CONTROLS_CONFIGURATION environment variable.

    Returns:
        List of SecurityControl objects parsed from the environment variable
    """
    config_string = asm_config._iast_security_controls
    if not config_string:
        return []

    try:
        controls = parse_security_controls_config(config_string)
        log.info("Loaded %s custom security controls from environment", len(controls))
        return controls
    except Exception:
        log.error("Failed to parse DD_IAST_SECURITY_CONTROLS_CONFIGURATION", exc_info=True)
        return []
