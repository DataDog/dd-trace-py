from types import SimpleNamespace

import pytest

import ddtrace._error_attribution
from ddtrace.internal.telemetry import inputs
from ddtrace.internal.utils import entry_points
from ddtrace.internal.utils.version import _pep440_to_semver
from ddtrace.internal.utils.version import version as product_version
from ddtrace.version import __version__


@pytest.fixture
def unresolved(monkeypatch):
    """Forget the registered and resolved library, as in a fresh interpreter."""
    monkeypatch.setattr(inputs, "_registered", None)
    monkeypatch.setattr(inputs, "_entry_point_looked_up", False)
    monkeypatch.setattr(inputs, "_entry_point", None)
    monkeypatch.setattr(inputs, "_library", None)
    monkeypatch.setattr(inputs, "_attribution", None)


class _Provider:
    code_roots = ("provider/",)

    @staticmethod
    def integration_of(filename):
        return "integration"


def _declared_by(*names):
    """Entry points of the library group declared by distributions ``names``, in path order."""
    return [
        (
            SimpleNamespace(value="provider", load=lambda: _Provider),
            SimpleNamespace(metadata={"Name": name}, version="%d.0.0" % i),
        )
        for i, name in enumerate(names, 1)
    ]


def test_ddtrace_declares_its_identity(unresolved):
    assert inputs.get_library() == ("ddtrace", __version__, product_version)


@pytest.mark.parametrize("version", [__version__, "1.7.1", "1.7.1rc2", "1.7.1.dev3+gf258c7d9", "1.7.1rc2.dev3"])
def test_product_version_formats_as_ddtrace_does(version):
    assert inputs._semver(version) == _pep440_to_semver(version)


def test_resolution_is_cached(unresolved, monkeypatch):
    inputs.get_library()
    inputs.is_library_code("x.py")
    monkeypatch.setattr(inputs, "entry_points_with_distributions", lambda group: pytest.fail("looked up twice"))

    assert inputs.get_library().name == "ddtrace"
    assert inputs.is_library_code("/site-packages/ddtrace/x.py")


def test_reading_the_identity_does_not_load_the_entry_point(unresolved, monkeypatch):
    found = _declared_by("lib")
    monkeypatch.setattr(found[0][0], "load", lambda: pytest.fail("loaded"))
    monkeypatch.setattr(inputs, "entry_points_with_distributions", lambda group: found)

    assert inputs.get_library() == ("lib", "1.0.0", "1.0.0")


@pytest.mark.parametrize(
    "declared,expected",
    [
        pytest.param(("ddtrace-testing", "ddtrace"), "ddtrace", id="prefers-other-than-ddtrace-testing"),
        pytest.param(("ddtrace_testing",), "ddtrace_testing", id="falls-back-to-ddtrace-testing"),
        pytest.param(("first", "second"), "first", id="first-on-the-path"),
    ],
)
def test_entry_point_choice(unresolved, monkeypatch, declared, expected):
    monkeypatch.setattr(inputs, "entry_points_with_distributions", lambda group: _declared_by(*declared))

    assert inputs.get_library().name == expected


def test_registration_beats_the_entry_point_and_the_first_wins(unresolved):
    inputs.get_library()

    inputs.register_library("first", "1.0.0", "1.0.0", code_roots=("first/",))
    inputs.register_library("second", "2.0.0", "2.0.0")

    assert inputs.get_library() == ("first", "1.0.0", "1.0.0")
    assert inputs.is_library_code("/site-packages/first/x.py")
    assert not inputs.is_library_code("/site-packages/ddtrace/internal/x.py")
    assert inputs.get_integration("/site-packages/ddtrace/contrib/internal/flask/patch.py") is None


def test_without_an_entry_point_the_library_is_unknown(unresolved, monkeypatch):
    monkeypatch.setattr(inputs, "entry_points_with_distributions", lambda group: [])

    assert inputs.get_library() == ("", "0.0.0", "0.0.0")
    assert not inputs.is_library_code("/site-packages/ddtrace/internal/x.py")
    assert inputs.get_integration("/site-packages/ddtrace/contrib/internal/flask/patch.py") is None


def test_an_entry_point_that_fails_to_load_attributes_nothing(unresolved, monkeypatch):
    found = _declared_by("lib")
    monkeypatch.setattr(found[0][0], "load", lambda: 1 / 0)
    monkeypatch.setattr(inputs, "entry_points_with_distributions", lambda group: found)

    assert not inputs.is_library_code("/site-packages/lib/x.py")
    assert inputs.get_integration("/site-packages/lib/x.py") is None


def test_entry_point_lookup_on_python_3_9(monkeypatch):
    """The Python 3.9 path, which walks the distributions, finds the same entry points."""
    found = entry_points.entry_points_with_distributions(inputs.GROUP)
    monkeypatch.setattr(entry_points, "_ENTRY_POINT_HAS_DIST", False)
    found_on_3_9 = entry_points.entry_points_with_distributions(inputs.GROUP)

    assert [(ep.name, ep.value) for ep, _ in found] == [("ddtrace", "ddtrace._error_attribution")]
    assert [(ep.name, ep.value, dist.metadata["Name"]) for ep, dist in found_on_3_9] == [
        (ep.name, ep.value, dist.metadata["Name"]) for ep, dist in found
    ]


def test_ddtrace_entry_point_provides_the_attribution():
    assert ddtrace._error_attribution.code_roots == ("ddtrace/", "/_ddtrace_")
    assert callable(ddtrace._error_attribution.integration_of)


@pytest.mark.subprocess(env={"DD_INSTRUMENTATION_TELEMETRY_ENABLED": "false"})
def test_importing_ddtrace_resolves_no_identity():
    import sys

    import ddtrace  # noqa: F401
    from ddtrace.internal.telemetry import inputs

    assert inputs._registered is None
    assert not inputs._entry_point_looked_up
    assert inputs._library is None
    assert inputs._attribution is None
    assert "ddtrace._error_attribution" not in sys.modules


@pytest.mark.subprocess(env={"DD_INSTRUMENTATION_TELEMETRY_ENABLED": "true"})
def test_enabled_telemetry_reads_the_identity_but_loads_no_attribution():
    import sys

    import ddtrace
    from ddtrace.internal.telemetry import inputs
    from ddtrace.internal.utils.version import version

    assert inputs._registered is None
    assert inputs._attribution is None
    assert "ddtrace._error_attribution" not in sys.modules
    assert inputs.get_library() == ("ddtrace", ddtrace.__version__, version)

    assert inputs.is_library_code("/site-packages/ddtrace/internal/x.py")
    assert "ddtrace._error_attribution" in sys.modules


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
def test_ddtrace_code_roots_cover_the_package_roots(unresolved, filename, expected):
    assert inputs.is_library_code(filename) is expected


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
def test_ddtrace_names_the_integration_of_its_paths(unresolved, filename, expected):
    assert inputs.get_integration(filename) == expected
