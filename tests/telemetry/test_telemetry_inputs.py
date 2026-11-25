import pytest

from ddtrace.internal import telemetry_inputs
from ddtrace.internal.utils.version import version as product_version
from ddtrace.version import __version__


def test_ddtrace_registers_its_identity():
    assert telemetry_inputs.get_library() == ("ddtrace", __version__, product_version)


def test_first_registration_wins(monkeypatch):
    monkeypatch.setattr(telemetry_inputs, "_library", None)

    telemetry_inputs.register_library("first", "1.0.0", "1.0.0")
    telemetry_inputs.register_library("second", "2.0.0", "2.0.0")

    assert telemetry_inputs.get_library() == ("first", "1.0.0", "1.0.0")


def test_unregistered_library_raises(monkeypatch):
    monkeypatch.setattr(telemetry_inputs, "_library", None)

    with pytest.raises(RuntimeError, match="register_library"):
        telemetry_inputs.get_library()
