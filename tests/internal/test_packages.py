import os

from ddtrace.internal.packages import get_distributions


def test_get_distributions():
    """use pkg_resources to validate package names and versions returned by get_distributions()"""
    import pkg_resources

    pkg_resources_ws = {pkg.project_name for pkg in pkg_resources.working_set}

    importlib_pkgs = set()
    for pkg in get_distributions():
        assert pkg.name
        assert pkg.version
        assert os.path.exists(pkg.path)
        # The package name in typing_extensions-4.x.x.dist-info/METADATA is set to `typing_extensions`
        # this is inconsistent with the package name found in pkg_resources. The block below corrects this.
        # The correct package name is typing-extensions.
        # The issue exists in pkgutil-resolve-name package.
        if pkg.name == "typing_extensions" and "typing-extensions" in pkg_resources_ws:
            importlib_pkgs.add("typing-extensions")
        elif pkg.name == "pkgutil_resolve_name" and "pkgutil-resolve-name" in pkg_resources_ws:
            importlib_pkgs.add("pkgutil-resolve-name")
        else:
            importlib_pkgs.add(pkg.name)

    # assert that pkg_resources and importlib.metadata return the same packages
    assert pkg_resources_ws == importlib_pkgs
