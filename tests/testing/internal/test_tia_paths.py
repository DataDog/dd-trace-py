from pathlib import Path
from unittest.mock import Mock

import ddtrace
from ddtrace.testing.internal.pytest._tia import _installed_source_aliases
from ddtrace.testing.internal.tia.paths import to_repo_rel


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def test_to_repo_rel_maps_aliased_site_packages_files(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    installed = root / ".venv" / "lib" / "site-packages" / "ddtrace"
    module = _touch(installed / "contrib" / "valkey.py")

    assert to_repo_rel(module, root) is None
    assert to_repo_rel(module, root, [(installed, "ddtrace")]) == "ddtrace/contrib/valkey.py"
    assert to_repo_rel(_touch(root / "tests" / "test_valkey.py"), root, [(installed, "ddtrace")]) == (
        "tests/test_valkey.py"
    )


def test_installed_source_aliases_require_a_tracked_ddtrace_package(tmp_path: Path) -> None:
    git = Mock()
    installed = Path(ddtrace.__file__).resolve().parent

    git.is_tracked.return_value = True
    assert _installed_source_aliases(tmp_path, git) == ((installed, "ddtrace"),)
    git.is_tracked.assert_called_once_with("ddtrace/__init__.py")

    git.is_tracked.return_value = False
    assert _installed_source_aliases(tmp_path, git) == ()


def test_installed_source_aliases_are_empty_when_ddtrace_is_imported_from_the_repository() -> None:
    git = Mock()
    git.is_tracked.return_value = True
    installed = Path(ddtrace.__file__).resolve().parent

    assert _installed_source_aliases(installed.parent, git) == ()
