import pytest

from ddtrace._error_attribution import integration_from_filename
from ddtrace.internal.telemetry_inputs import library
from ddtrace.internal.utils.version import version as product_version
from ddtrace.version import __version__


def test_ddtrace_registers_its_identity():
    registered = library.get_library()

    assert registered[:3] == ("ddtrace", __version__, product_version)
    assert registered.integration_of is integration_from_filename


def test_first_registration_wins(monkeypatch):
    monkeypatch.setattr(library, "_library", None)

    library.register_library("first", "1.0.0", "1.0.0")
    library.register_library("second", "2.0.0", "2.0.0")

    assert library.get_library()[:3] == ("first", "1.0.0", "1.0.0")


def test_unregistered_library_raises(monkeypatch):
    monkeypatch.setattr(library, "_library", None)

    with pytest.raises(RuntimeError, match="register_library"):
        library.get_library()


def test_library_attributes_no_code_by_default(monkeypatch):
    monkeypatch.setattr(library, "_library", None)
    library.register_library("lib", "1.0.0", "1.0.0")

    assert library.get_library().code_roots == ()
    assert not library.is_library_code("/site-packages/ddtrace/internal/x.py")
    assert library.get_integration("/site-packages/ddtrace/contrib/internal/flask/patch.py") is None


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("/site-packages/ddtrace/internal/telemetry/writer.py", True),
        ("/site-packages/ddtrace/contrib/internal/flask/patch.py", True),
        ("/site-packages/_ddtrace_internal/telemetry/writer.py", True),
        ("/site-packages/_ddtrace_coverage/code.py", True),
        ("/site-packages/_ddtrace_native/__init__.py", True),
        ("/site-packages/flask/app.py", False),
        ("/app/ddtrace.py", False),
    ],
)
def test_ddtrace_code_roots_cover_the_package_roots(filename, expected):
    assert library.is_library_code(filename) is expected


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("/site-packages/ddtrace/contrib/flask/patch.py", "flask"),
        ("/site-packages/ddtrace/contrib/internal/flask/patch.py", "flask"),
        ("/site-packages/ddtrace/contrib/internal/flask/sub/mod.py", "flask"),
        ("/site-packages/ddtrace/contrib/patch.py", None),
        ("/site-packages/ddtrace/internal/telemetry/writer.py", None),
        ("/site-packages/other/ddtrace/x/contrib/flask/patch.py", None),
        ("/site-packages/_ddtrace_internal/contrib/flask/patch.py", None),
    ],
)
def test_ddtrace_names_the_integration_of_its_paths(filename, expected):
    assert library.get_integration(filename) == expected
