from ddtrace.internal.packages import get_distributions


def test_get_distributions():
    """use pkg_resources to validate package names and versions returned by get_distributions()"""
    import pkg_resources

    pkg_resources_ws = {pkg.project_name for pkg in pkg_resources.working_set}

    importlib_pkgs = {pkg.name for pkg in get_distributions()}
    # The package name in typing_extensions-4.x.x.dist-info/METADATA is set to `typing_extensions`
    # this is inconsistent with the package name found in pkg_resources. The block below corrects this.
    # The correct package name is typing-extensions.
    if "typing_extensions" in importlib_pkgs and "typing-extensions" in pkg_resources_ws:
        importlib_pkgs.discard("typing_extensions")
        importlib_pkgs.add("typing-extensions")

    # assert that pkg_resources and importlib.metadata return the same packages
    assert pkg_resources_ws == importlib_pkgs
