from ddtrace.internal.packages import Distribution
from ddtrace.internal.packages import get_distributions
from ddtrace.internal.utils.version import parse_version


def test_get_distributions():
    """use pkg_resources to validate package names and versions returned by get_distributions()"""
    import pkg_resources

    pkg_resources_ws = []
    for pkg in pkg_resources.working_set:
        # Rename typing-extension in working_set to match the package name returned
        # by get_distributions()
        if pkg.project_name == "typing-extensions" and parse_version(pkg.version) >= (4, 0):
            pkg.project_name = "typing_extensions"
        pkg_resources_ws.append(Distribution(pkg.project_name, pkg.version))

    importlib_pkg = get_distributions()
    assert importlib_pkg

    pkg_resources_ws.sort(key=lambda x: x.name)
    importlib_pkg.sort(key=lambda x: x.name)
    assert importlib_pkg == pkg_resources_ws
