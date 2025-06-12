"""Tests for DD_IAST_SECURITY_CONTROLS_CONFIGURATION environment variable functionality."""
import functools
import os
from unittest.mock import patch

import pytest

from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
from ddtrace.appsec._iast.secure_marks.configuration import SC_SANITIZER
from ddtrace.appsec._iast.secure_marks.configuration import SC_VALIDATOR
from ddtrace.appsec._iast.secure_marks.configuration import VULNERABILITY_TYPE_MAPPING
from ddtrace.appsec._iast.secure_marks.configuration import SecurityControl
from ddtrace.appsec._iast.secure_marks.configuration import get_security_controls_from_env
from ddtrace.appsec._iast.secure_marks.configuration import parse_parameters
from ddtrace.appsec._iast.secure_marks.configuration import parse_security_controls_config
from ddtrace.appsec._iast.secure_marks.configuration import parse_vulnerability_types
from ddtrace.appsec._iast.secure_marks.sanitizers import create_sanitizer
from ddtrace.appsec._iast.secure_marks.validators import create_validator
from tests.utils import override_global_config


def test_vulnerability_type_mapping():
    """Test that all expected vulnerability types are mapped correctly."""
    expected_types = {
        "CODE_INJECTION",
        "COMMAND_INJECTION",
        "HEADER_INJECTION",
        "UNVALIDATED_REDIRECT",
        "INSECURE_COOKIE",
        "NO_HTTPONLY_COOKIE",
        "NO_SAMESITE_COOKIE",
        "PATH_TRAVERSAL",
        "SQL_INJECTION",
        "SQLI",  # Alias
        "SSRF",
        "STACKTRACE_LEAK",
        "WEAK_CIPHER",
        "WEAK_HASH",
        "WEAK_RANDOMNESS",
        "XSS",
    }

    assert set(VULNERABILITY_TYPE_MAPPING.keys()) == expected_types
    assert VULNERABILITY_TYPE_MAPPING["SQLI"] == VulnerabilityType.SQL_INJECTION


def test_parse_vulnerability_types_single():
    """Test parsing a single vulnerability type."""
    result = parse_vulnerability_types("COMMAND_INJECTION")
    assert result == [VulnerabilityType.COMMAND_INJECTION]


def test_parse_vulnerability_types_multiple():
    """Test parsing multiple vulnerability types."""
    result = parse_vulnerability_types("COMMAND_INJECTION,XSS,SQLI")
    expected = [
        VulnerabilityType.COMMAND_INJECTION,
        VulnerabilityType.XSS,
        VulnerabilityType.SQL_INJECTION,
    ]
    assert result == expected


def test_parse_vulnerability_types_all():
    """Test parsing '*' for all vulnerability types."""
    result = parse_vulnerability_types("*")
    assert len(result) == len(VULNERABILITY_TYPE_MAPPING)
    assert VulnerabilityType.COMMAND_INJECTION in result
    assert VulnerabilityType.XSS in result


def test_parse_vulnerability_types_invalid():
    """Test parsing invalid vulnerability type raises error."""
    with pytest.raises(ValueError, match="Unknown vulnerability type: INVALID_TYPE"):
        parse_vulnerability_types("INVALID_TYPE")


def test_parse_parameter_positions_empty():
    """Test parsing empty parameter positions."""
    assert parse_parameters("") == []
    assert parse_parameters("   ") == []


def test_parse_parameter_positions_single():
    """Test parsing single parameter position."""
    assert parse_parameters("0") == [0]
    assert parse_parameters("3") == [3]


def test_parse_parameter_positions_multiple():
    """Test parsing multiple parameter positions."""
    assert parse_parameters("0,1,3") == [0, 1, 3]
    assert parse_parameters("1, 2, 4") == [1, 2, 4]  # With spaces


def test_parse_parameter_positions_invalid():
    """Test parsing invalid parameter positions raises error."""
    with pytest.raises(ValueError, match="Invalid parameter positions"):
        parse_parameters("0,invalid,2")


def test_security_control_creation():
    """Test SecurityControl object creation."""
    control = SecurityControl(
        control_type=SC_VALIDATOR,
        vulnerability_types=[VulnerabilityType.COMMAND_INJECTION],
        module_path="shlex",
        method_name="quote",
    )

    assert control.control_type == SC_VALIDATOR
    assert control.vulnerability_types == [VulnerabilityType.COMMAND_INJECTION]
    assert control.module_path == "shlex"
    assert control.method_name == "quote"
    assert control.parameters == []


def test_security_control_invalid_type():
    """Test SecurityControl with invalid control type raises error."""
    with pytest.raises(ValueError, match="Invalid control type: INVALID"):
        SecurityControl(
            control_type="INVALID",
            vulnerability_types=[VulnerabilityType.COMMAND_INJECTION],
            module_path="shlex",
            method_name="quote",
        )


def test_parse_security_controls_config_empty():
    """Test parsing empty configuration."""
    assert parse_security_controls_config("") == []
    assert parse_security_controls_config("   ") == []


def test_parse_security_controls_config_single():
    """Test parsing single security control configuration."""
    config = "INPUT_VALIDATOR:COMMAND_INJECTION:shlex:quote"
    result = parse_security_controls_config(config)

    assert len(result) == 1
    control = result[0]
    assert control.control_type == SC_VALIDATOR
    assert control.vulnerability_types == [VulnerabilityType.COMMAND_INJECTION]
    assert control.module_path == "shlex"
    assert control.method_name == "quote"


def test_parse_security_controls_config_multiple():
    """Test parsing multiple security control configurations."""
    config = "INPUT_VALIDATOR:COMMAND_INJECTION:shlex:quote;" "SANITIZER:XSS,SQLI:html:escape"
    result = parse_security_controls_config(config)

    assert len(result) == 2

    # First control
    assert result[0].control_type == SC_VALIDATOR
    assert result[0].vulnerability_types == [VulnerabilityType.COMMAND_INJECTION]
    assert result[0].module_path == "shlex"
    assert result[0].method_name == "quote"

    # Second control
    assert result[1].control_type == SC_SANITIZER
    assert result[1].vulnerability_types == [VulnerabilityType.XSS, VulnerabilityType.SQL_INJECTION]
    assert result[1].module_path == "html"
    assert result[1].method_name == "escape"


def test_parse_security_controls_config_with_parameters():
    """Test parsing security control configuration with parameters."""
    config = "INPUT_VALIDATOR:COMMAND_INJECTION:bar.foo:CustomValidator.validate:0,1"
    result = parse_security_controls_config(config)

    assert len(result) == 1
    control = result[0]
    assert control.control_type == SC_VALIDATOR
    assert control.vulnerability_types == [VulnerabilityType.COMMAND_INJECTION]
    assert control.module_path == "bar.foo"
    assert control.method_name == "CustomValidator.validate"
    assert control.parameters == [0, 1]


def test_parse_security_controls_config_with_all_vulnerabilities():
    """Test parsing security control configuration with '*' for all vulnerabilities."""
    config = "SANITIZER:*:custom.sanitizer:clean_all"
    result = parse_security_controls_config(config)

    assert len(result) == 1
    control = result[0]
    assert control.control_type == SC_SANITIZER
    assert len(control.vulnerability_types) == len(VULNERABILITY_TYPE_MAPPING)
    assert VulnerabilityType.COMMAND_INJECTION in control.vulnerability_types
    assert VulnerabilityType.XSS in control.vulnerability_types


def test_get_security_controls_from_env_empty():
    """Test getting security controls from empty environment variable."""
    result = get_security_controls_from_env()
    assert result == []


def test_get_security_controls_from_env_valid():
    """Test getting security controls from valid environment variable."""
    with override_global_config(
        dict(_iast_security_controls="INPUT_VALIDATOR:COMMAND_INJECTION:shlex:quote;SANITIZER:XSS:html:escape")
    ):
        result = get_security_controls_from_env()

    assert len(result) == 2
    assert result[0].control_type == SC_VALIDATOR
    assert result[0].module_path == "shlex"
    assert result[1].control_type == SC_SANITIZER
    assert result[1].module_path == "html"


@patch.dict(os.environ, {"DD_IAST_SECURITY_CONTROLS_CONFIGURATION": "INVALID:FORMAT"})
def test_get_security_controls_from_env_invalid():
    """Test getting security controls from invalid environment variable."""
    result = get_security_controls_from_env()
    assert result == []  # Should return empty list on parse error


def test_create_generic_sanitizer():
    """Test creating a generic sanitizer function."""
    vulnerability_types = [VulnerabilityType.COMMAND_INJECTION, VulnerabilityType.XSS]
    sanitizer = functools.partial(create_sanitizer, vulnerability_types)

    assert callable(sanitizer)

    # Mock wrapped function
    def mock_wrapped(*args, **kwargs):
        return "sanitized_output"

    # Test the sanitizer
    result = sanitizer(mock_wrapped, None, ["input"], {})
    assert result == "sanitized_output"


def test_create_generic_validator():
    """Test creating a generic validator function."""
    vulnerability_types = [VulnerabilityType.COMMAND_INJECTION]
    validator = functools.partial(create_validator, vulnerability_types, None)

    assert callable(validator)

    # Mock wrapped function
    def mock_wrapped(*args, **kwargs):
        return True

    # Test the validator
    result = validator(mock_wrapped, None, ["input"], {})
    assert result is True


def test_create_generic_validator_with_positions():
    """Test creating a generic validator function with specific parameter positions."""
    vulnerability_types = [VulnerabilityType.COMMAND_INJECTION]
    parameter_positions = [0, 2]
    validator = functools.partial(create_validator, vulnerability_types, parameter_positions)

    assert callable(validator)

    # Mock wrapped function
    def mock_wrapped(*args, **kwargs):
        return True

    # Test the validator
    result = validator(mock_wrapped, None, ["input1", "input2", "input3"], {})
    assert result is True


def test_input_validator_example():
    """Test input validator example."""
    config = "INPUT_VALIDATOR:COMMAND_INJECTION:bar.foo.CustomInputValidator:validate"
    result = parse_security_controls_config(config)

    assert len(result) == 1
    control = result[0]
    assert control.control_type == SC_VALIDATOR
    assert control.vulnerability_types == [VulnerabilityType.COMMAND_INJECTION]
    assert control.module_path == "bar.foo.CustomInputValidator"
    assert control.method_name == "validate"


def test_input_validator_with_positions_example():
    """Test input validator with parameter positions example."""
    config = "INPUT_VALIDATOR:COMMAND_INJECTION:bar.foo.CustomInputValidator:validate:1,2"
    result = parse_security_controls_config(config)

    assert len(result) == 1
    control = result[0]
    assert control.control_type == SC_VALIDATOR
    assert control.parameters == [1, 2]


def test_multiple_vulnerabilities_example():
    """Test multiple vulnerabilities example."""
    config = "INPUT_VALIDATOR:COMMAND_INJECTION,CODE_INJECTION:bar.foo.CustomInputValidator:validate"
    result = parse_security_controls_config(config)

    assert len(result) == 1
    control = result[0]
    assert control.vulnerability_types == [VulnerabilityType.COMMAND_INJECTION, VulnerabilityType.CODE_INJECTION]


def test_all_vulnerabilities_example():
    """Test all vulnerabilities example."""
    config = "INPUT_VALIDATOR:*:bar.foo.CustomInputValidator:validate"
    result = parse_security_controls_config(config)

    assert len(result) == 1
    control = result[0]
    assert len(control.vulnerability_types) > 10  # Should include all vulnerability types


def test_sanitizer_example():
    """Test sanitizer example."""
    config = "SANITIZER:COMMAND_INJECTION:bar.foo.CustomSanitizer:sanitize"
    result = parse_security_controls_config(config)

    assert len(result) == 1
    control = result[0]
    assert control.control_type == SC_SANITIZER
    assert control.vulnerability_types == [VulnerabilityType.COMMAND_INJECTION]
    assert control.module_path == "bar.foo.CustomSanitizer"
    assert control.method_name == "sanitize"


def test_nodejs_input_validator_example():
    """Test Node.js input validator example (adapted for Python)."""
    config = "INPUT_VALIDATOR:COMMAND_INJECTION:bar.foo.custom_input_validator:validate"
    result = parse_security_controls_config(config)

    assert len(result) == 1
    control = result[0]
    assert control.control_type == SC_VALIDATOR
    assert control.vulnerability_types == [VulnerabilityType.COMMAND_INJECTION]
    assert control.module_path == "bar.foo.custom_input_validator"
    assert control.method_name == "validate"


def test_complex_multi_control_example():
    """Test complex example with multiple controls."""
    config = (
        "INPUT_VALIDATOR:COMMAND_INJECTION,XSS:com.example:Validator.validateInput:0,1;"
        "SANITIZER:*:com.example:Sanitizer.sanitizeInput"
    )
    result = parse_security_controls_config(config)

    assert len(result) == 2

    # First control - Input validator
    validator_control = result[0]
    assert validator_control.control_type == SC_VALIDATOR
    assert validator_control.vulnerability_types == [VulnerabilityType.COMMAND_INJECTION, VulnerabilityType.XSS]
    assert validator_control.module_path == "com.example"
    assert validator_control.method_name == "Validator.validateInput"
    assert validator_control.parameters == [0, 1]

    # Second control - Sanitizer for all vulnerabilities
    sanitizer_control = result[1]
    assert sanitizer_control.control_type == SC_SANITIZER
    assert len(sanitizer_control.vulnerability_types) > 10
    assert sanitizer_control.module_path == "com.example"
    assert sanitizer_control.method_name == "Sanitizer.sanitizeInput"


def test_complex_example():
    """Test complex example with multiple controls."""
    config = (
        "INPUT_VALIDATOR:COMMAND_INJECTION,XSS:custom.validator:validate_input:0,1;"
        "SANITIZER:*:custom.sanitizer:sanitize_all"
    )
    result = parse_security_controls_config(config)

    assert len(result) == 2

    # Input validator
    validator = result[0]
    assert validator.control_type == SC_VALIDATOR
    assert validator.vulnerability_types == [VulnerabilityType.COMMAND_INJECTION, VulnerabilityType.XSS]
    assert validator.parameters == [0, 1]

    # Sanitizer for all
    sanitizer = result[1]
    assert sanitizer.control_type == SC_SANITIZER
    assert len(sanitizer.vulnerability_types) >= 10
