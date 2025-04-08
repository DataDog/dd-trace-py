import pytest

from ddtrace.internal.packages import _third_party_packages
from ddtrace.internal.packages import filename_to_package
from ddtrace.internal.packages import get_distributions


def test_get_distributions():
    """use pkg_resources to validate package names and versions returned by get_distributions()"""
    import pkg_resources

    pkg_resources_ws = {pkg.project_name for pkg in pkg_resources.working_set}

    importlib_pkgs = set()
    for pkg in get_distributions():
        assert pkg.name
        assert pkg.version
        # The package name in typing_extensions-4.x.x.dist-info/METADATA is set to `typing_extensions`
        # this is inconsistent with the package name found in pkg_resources. The block below corrects this.
        # The correct package name is typing-extensions.
        # The issue exists in pkgutil-resolve-name package.
        if pkg.name == "typing_extensions" and "typing-extensions" in pkg_resources_ws:
            importlib_pkgs.add("typing-extensions")
        elif pkg.name == "pkgutil_resolve_name" and "pkgutil-resolve-name" in pkg_resources_ws:
            importlib_pkgs.add("pkgutil-resolve-name")
        elif pkg.name == "importlib_metadata" and "importlib-metadata" in pkg_resources_ws:
            importlib_pkgs.add("importlib-metadata")
        elif pkg.name == "importlib-metadata" and "importlib_metadata" in pkg_resources_ws:
            importlib_pkgs.add("importlib_metadata")
        elif pkg.name == "importlib-resources" and "importlib_resources" in pkg_resources_ws:
            importlib_pkgs.add("importlib_resources")
        elif pkg.name == "importlib_resources" and "importlib-resources" in pkg_resources_ws:
            importlib_pkgs.add("importlib-resources")
        else:
            importlib_pkgs.add(pkg.name)

    # assert that pkg_resources and importlib.metadata return the same packages
    assert pkg_resources_ws == importlib_pkgs


def test_filename_to_package():
    # type: (...) -> None
    package = filename_to_package(__file__)
    assert package is None or package.name == "ddtrace"
    package = filename_to_package(pytest.__file__)
    assert package and package.name == "pytest"

    import httpretty

    package = filename_to_package(httpretty.__file__)
    assert package and package.name == "httpretty"

    import google.protobuf.internal as gp

    package = filename_to_package(gp.__file__)
    assert package and package.name == "protobuf"

    try:
        package = filename_to_package("You may be wondering how I got here even though I am not a file.")
    except Exception:
        pytest.fail("filename_to_package should not raise an exception when given a non-file path")


def test_third_party_packages():
    assert 4000 < len(_third_party_packages()) < 5000

    assert "requests" in _third_party_packages()
    assert "nota3rdparty" not in _third_party_packages()


@pytest.mark.subprocess(
    env={
        "DD_THIRD_PARTY_DETECTION_INCLUDES": "myfancypackage,myotherfancypackage",
        "DD_THIRD_PARTY_DETECTION_EXCLUDES": "requests",
    }
)
def test_third_party_packages_excludes_includes():
    from ddtrace.internal.packages import _third_party_packages

    assert {"myfancypackage", "myotherfancypackage"} < _third_party_packages()
    assert "requests" not in _third_party_packages()
