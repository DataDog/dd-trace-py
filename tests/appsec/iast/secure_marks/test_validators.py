"""Tests for IAST secure marks validators."""

import functools
import types
from unittest import mock

import pytest
from wrapt import FunctionWrapper

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
from ddtrace.appsec._iast._taint_tracking import get_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast.secure_marks.validators import cmdi_validator
from ddtrace.appsec._iast.secure_marks.validators import create_validator
from ddtrace.appsec._iast.secure_marks.validators import path_traversal_validator
from ddtrace.appsec._iast.secure_marks.validators import sqli_validator
from ddtrace.internal.security._patch_utils import wrap_object


def test_path_traversal_validator():
    """Test that path_traversal_validator marks filenames as safe from path traversal."""
    # Create a tainted filename
    filename = "../../etc/passwd"
    tainted = taint_pyobject(
        pyobject=filename,
        source_name="test_add_aspect_tainting_left_hand",
        source_value=filename,
        source_origin=OriginType.PARAMETER,
    )

    # Mock the validator function
    validator = mock.Mock(return_value=True)

    # Apply the validator
    path_traversal_validator(validator, None, [tainted], {})

    # Verify the argument is marked as secure
    ranges = get_ranges(tainted)
    assert ranges
    for _range in ranges:
        assert _range.has_secure_mark(VulnerabilityType.PATH_TRAVERSAL)


def test_sql_quote_validator():
    """Test that sqli_validator marks values as safe from SQL injection."""
    # Create a tainted SQL value
    sql = "'; DROP TABLE users; --"
    tainted = taint_pyobject(
        pyobject=sql,
        source_name="test_add_aspect_tainting_left_hand",
        source_value=sql,
        source_origin=OriginType.PARAMETER,
    )

    # Mock the validator function
    validator = mock.Mock(return_value=True)

    # Apply the validator
    sqli_validator(validator, None, [tainted], {})

    # Verify the argument is marked as secure
    ranges = get_ranges(tainted)
    assert ranges
    for _range in ranges:
        assert _range.has_secure_mark(VulnerabilityType.SQL_INJECTION)


def test_command_quote_validator():
    """Test that cmdi_validator marks values as safe from command injection."""
    # Create a tainted command
    cmd = "; rm -rf /"
    tainted = taint_pyobject(
        pyobject=cmd,
        source_name="test_add_aspect_tainting_left_hand",
        source_value=cmd,
        source_origin=OriginType.PARAMETER,
    )

    # Mock the validator function
    validator = mock.Mock(return_value=True)

    # Apply the validator
    cmdi_validator(validator, None, [tainted], {})

    # Verify the argument is marked as secure
    ranges = get_ranges(tainted)
    assert ranges
    for _range in ranges:
        assert _range.has_secure_mark(VulnerabilityType.COMMAND_INJECTION)


def _validators_module(hook):
    """Build a module whose validators are wrapped the same way custom security controls wrap them."""
    module = types.ModuleType("custom_validators")

    def validate(command, flag=""):
        return True

    class Validator:
        def validate(self, command, flag=""):
            return True

        @classmethod
        def class_validate(cls, command, flag=""):
            return True

        @staticmethod
        def static_validate(command, flag=""):
            return True

    module.validate = validate
    module.Validator = Validator
    for name in ("validate", "Validator.validate", "Validator.class_validate", "Validator.static_validate"):
        wrap_object(module, name, FunctionWrapper, (hook,))
    return module


_BINDINGS = {
    "function": lambda module: module.validate,
    "bound_method": lambda module: module.Validator().validate,
    "unbound_method": lambda module: functools.partial(module.Validator.validate, module.Validator()),
    "classmethod": lambda module: module.Validator.class_validate,
    "classmethod_instance": lambda module: module.Validator().class_validate,
    "staticmethod": lambda module: module.Validator.static_validate,
    "staticmethod_instance": lambda module: module.Validator().static_validate,
}

_CALL_FORMS = {
    "positional": lambda validator, command, flag: validator(command, flag),
    "mixed": lambda validator, command, flag: validator(command, flag=flag),
    "keyword": lambda validator, command, flag: validator(command=command, flag=flag),
}


@pytest.mark.parametrize("position", [0, 1])
@pytest.mark.parametrize("call_form", list(_CALL_FORMS))
@pytest.mark.parametrize("binding", list(_BINDINGS))
def test_validator_with_positions_marks_only_configured_parameters(binding, call_form, position):
    """With parameter positions, only arguments bound to those positions are marked, however they are passed."""
    command, flag = (
        taint_pyobject(
            pyobject=value,
            source_name="test_validator_with_positions",
            source_value=value,
            source_origin=OriginType.PARAMETER,
        )
        for value in ("; rm -rf /", "--force")
    )
    validated, unvalidated = (command, flag) if position == 0 else (flag, command)
    hook = functools.partial(create_validator, [VulnerabilityType.COMMAND_INJECTION], [position])
    validator = _BINDINGS[binding](_validators_module(hook))

    assert _CALL_FORMS[call_form](validator, command, flag)

    validated_ranges = get_ranges(validated)
    assert validated_ranges
    assert all(r.has_secure_mark(VulnerabilityType.COMMAND_INJECTION) for r in validated_ranges)
    unvalidated_ranges = get_ranges(unvalidated)
    assert unvalidated_ranges
    assert not any(r.has_secure_mark(VulnerabilityType.COMMAND_INJECTION) for r in unvalidated_ranges)
