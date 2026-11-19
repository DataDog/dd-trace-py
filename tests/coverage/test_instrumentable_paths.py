from pathlib import Path

from ddtrace.internal.coverage.code import ModuleCodeCollector


REPO = Path("/repo")
SITE_PACKAGES = REPO / ".venv" / "lib" / "python3.12" / "site-packages"


def _collector(include_paths: list[Path], exclude_paths: list[Path]) -> ModuleCodeCollector:
    collector = ModuleCodeCollector.__new__(ModuleCodeCollector)
    collector._include_paths = include_paths
    collector._exclude_paths = exclude_paths
    return collector


def test_site_packages_inside_an_included_repository_are_not_instrumented() -> None:
    collector = _collector([REPO], [SITE_PACKAGES])

    assert collector._is_instrumentable_path(REPO / "tests" / "test_module.py")
    assert not collector._is_instrumentable_path(SITE_PACKAGES / "ddtrace" / "tracer.py")


def test_an_include_path_inside_site_packages_opts_that_package_in() -> None:
    installed = SITE_PACKAGES / "ddtrace"
    collector = _collector([REPO, installed], [SITE_PACKAGES, installed / "internal" / "coverage"])

    assert collector._is_instrumentable_path(installed / "contrib" / "valkey.py")
    # Exclusions nested inside the included package still apply.
    assert not collector._is_instrumentable_path(installed / "internal" / "coverage" / "code.py")
    # Other installed packages stay excluded.
    assert not collector._is_instrumentable_path(SITE_PACKAGES / "valkey" / "client.py")
