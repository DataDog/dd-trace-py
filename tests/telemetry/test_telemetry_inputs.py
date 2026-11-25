import pytest

from ddtrace.internal.telemetry_inputs import library
from ddtrace.internal.utils.version import version as product_version
from ddtrace.version import __version__


def test_ddtrace_registers_its_identity():
    assert library.get_library() == ("ddtrace", __version__, product_version)


def test_first_registration_wins(monkeypatch):
    monkeypatch.setattr(library, "_library", None)

    library.register_library("first", "1.0.0", "1.0.0")
    library.register_library("second", "2.0.0", "2.0.0")

    assert library.get_library() == ("first", "1.0.0", "1.0.0")


def test_unregistered_library_raises(monkeypatch):
    monkeypatch.setattr(library, "_library", None)

    with pytest.raises(RuntimeError, match="register_library"):
        library.get_library()
