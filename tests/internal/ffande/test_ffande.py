"""Tests for FFE (Feature Flagging and Experimentation) product."""
import json

from ddtrace.internal.openfeature._native import is_available
from ddtrace.internal.openfeature._native import process_ffe_configuration


def test_native_module_available():
    """Test that the native module is available after build."""
    assert is_available is True


def test_process_ffe_configuration_success():
    """Test successful FFE configuration processing."""
    config = {"rules": [{"flag": "test_flag", "enabled": True}]}
    config_bytes = json.dumps(config).encode("utf-8")

    result = process_ffe_configuration(config_bytes)
    assert result is True


def test_process_ffe_configuration_empty():
    """Test FFE configuration with empty bytes."""
    result = process_ffe_configuration(b"")
    assert result is False


def test_process_ffe_configuration_invalid_utf8():
    """Test FFE configuration with invalid UTF-8."""
    result = process_ffe_configuration(b"\xFF\xFE\xFD")
    assert result is False
