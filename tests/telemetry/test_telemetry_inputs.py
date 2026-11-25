import pytest

from ddtrace._error_attribution import integration_from_filename
from ddtrace.internal.telemetry_inputs import configurations
from ddtrace.internal.telemetry_inputs import dependencies
from ddtrace.internal.telemetry_inputs import endpoints
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


@pytest.fixture
def unregistered_dependencies(monkeypatch):
    monkeypatch.setattr(dependencies, "_is_enabled", None)


def test_dependency_metadata_is_disabled_by_default(unregistered_dependencies):
    assert dependencies.dependency_metadata_enabled() is False


def test_ddtrace_registers_dependency_metadata_from_the_appsec_settings(monkeypatch):
    from ddtrace.internal.settings.appsec_telemetry import config as appsec_telemetry_config

    monkeypatch.setattr(appsec_telemetry_config, "SCA_ENABLED", True)
    assert dependencies.dependency_metadata_enabled() is True
    monkeypatch.setattr(appsec_telemetry_config, "SCA_ENABLED", None)
    assert dependencies.dependency_metadata_enabled() is False


def test_first_dependency_metadata_registration_wins(unregistered_dependencies):
    dependencies.register_dependency_metadata(lambda: True)
    dependencies.register_dependency_metadata(lambda: False)

    assert dependencies.dependency_metadata_enabled() is True


@pytest.fixture
def unregistered_endpoints(monkeypatch):
    monkeypatch.setattr(endpoints, "_is_enabled", None)
    monkeypatch.setattr(endpoints, "_get_limit", None)


def test_endpoint_collection_defaults(unregistered_endpoints):
    from ddtrace.internal.settings.appsec_telemetry import AppSecTelemetryConfig

    defaults = AppSecTelemetryConfig({})

    assert endpoints.endpoint_collection_enabled() is defaults.ENDPOINT_COLLECTION_ENABLED is True
    assert endpoints.endpoint_collection_limit() == defaults.ENDPOINT_COLLECTION_LIMIT == 300


def test_ddtrace_registers_endpoint_collection_from_the_appsec_settings(monkeypatch):
    from ddtrace.internal.settings.appsec_telemetry import config as appsec_telemetry_config

    monkeypatch.setattr(appsec_telemetry_config, "ENDPOINT_COLLECTION_ENABLED", False)
    monkeypatch.setattr(appsec_telemetry_config, "ENDPOINT_COLLECTION_LIMIT", 42)

    assert endpoints.endpoint_collection_enabled() is False
    assert endpoints.endpoint_collection_limit() == 42


def test_first_endpoint_collection_registration_wins(unregistered_endpoints):
    endpoints.register_endpoint_collection(lambda: False, lambda: 1)
    endpoints.register_endpoint_collection(lambda: True, lambda: 2)

    assert endpoints.endpoint_collection_enabled() is False
    assert endpoints.endpoint_collection_limit() == 1


def test_ddtrace_registers_the_appsec_configuration_for_reporting():
    from ddtrace.internal.settings.appsec_telemetry import config as appsec_telemetry_config

    assert [get_config() for get_config in configurations.get_configurations()] == [appsec_telemetry_config]
