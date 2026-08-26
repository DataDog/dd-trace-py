from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

import pytest

from scripts._testenv import UvTestEnvironmentError
from scripts._testenv import ensure_environment
from scripts._testenv import environment_commands
from scripts._testenv import environment_is_current
from scripts._testenv import prepare_environment


PYTHON = "3.11"


def _prepared(
    tmp_path: Path,
    contents: str = "alpha==1.0\n",
    environment_hash: str = "example",
):
    lockfile = tmp_path / f"{environment_hash}.txt"
    lockfile.write_text(contents)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'example'\nversion = '1.0'\n")
    return prepare_environment(
        tmp_path,
        environment_hash=environment_hash,
        lockfile=lockfile.relative_to(tmp_path),
    )


def _install_fake_environment(root: Path, prepared) -> None:
    venv = root / prepared.path
    (venv / "bin").mkdir(parents=True, exist_ok=True)
    (venv / "bin/python").touch()
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
    site_packages = venv / f"lib/python{PYTHON}/site-packages"
    site_packages.mkdir(parents=True, exist_ok=True)
    metadata = site_packages / "alpha-1.0.dist-info/METADATA"
    metadata.parent.mkdir(exist_ok=True)
    metadata.write_text("Name: alpha\nVersion: 1.0\n")


def _build(root: Path, prepared, calls: list[list[str]]):
    def run(command: list[str]) -> None:
        calls.append(command)
        if command[1:3] == ["pip", "install"]:
            _install_fake_environment(root, prepared)

    return run


def _ensure(root: Path, prepared, run, reuse_current: bool = True):
    return ensure_environment(
        root,
        prepared,
        python=PYTHON,
        reuse_current=reuse_current,
        run=run,
    )


def test_missing_and_incomplete_environments_are_not_current(tmp_path):
    prepared = _prepared(tmp_path)

    assert not environment_is_current(tmp_path, prepared, python=PYTHON)
    venv = tmp_path / prepared.path
    venv.mkdir(parents=True)
    (venv / "pyvenv.cfg").touch()
    assert not environment_is_current(tmp_path, prepared, python=PYTHON)


def test_lock_change_makes_environment_stale(tmp_path):
    prepared = _prepared(tmp_path)
    _ensure(tmp_path, prepared, _build(tmp_path, prepared, []))
    changed = _prepared(tmp_path, contents="alpha==2.0\n")

    assert changed.path != prepared.path
    assert not environment_is_current(tmp_path, changed, python=PYTHON)


def test_current_environment_is_reused(tmp_path):
    prepared = _prepared(tmp_path)
    calls = []
    run = _build(tmp_path, prepared, calls)

    assert _ensure(tmp_path, prepared, run)
    assert not _ensure(tmp_path, prepared, run)
    assert len(calls) == 2


def test_environment_is_rebuilt_unless_reuse_is_explicit(tmp_path):
    prepared = _prepared(tmp_path)
    calls = []
    run = _build(tmp_path, prepared, calls)

    assert _ensure(tmp_path, prepared, run, reuse_current=False)
    assert _ensure(tmp_path, prepared, run, reuse_current=False)
    assert not _ensure(tmp_path, prepared, run, reuse_current=True)
    assert len(calls) == 4


def test_installed_package_drift_makes_environment_stale(tmp_path):
    prepared = _prepared(tmp_path)
    _ensure(tmp_path, prepared, _build(tmp_path, prepared, []))
    metadata = tmp_path / prepared.path / f"lib/python{PYTHON}/site-packages/beta-1.0.dist-info/METADATA"
    metadata.parent.mkdir()
    metadata.write_text("Name: beta\nVersion: 1.0\n")

    assert not environment_is_current(tmp_path, prepared, python=PYTHON)


def test_failed_build_is_not_marked_current(tmp_path):
    prepared = _prepared(tmp_path)

    def fail(_command: list[str]) -> None:
        raise UvTestEnvironmentError("install failed")

    with pytest.raises(UvTestEnvironmentError, match="install failed"):
        _ensure(tmp_path, prepared, fail)
    assert not environment_is_current(tmp_path, prepared, python=PYTHON)


def test_same_environment_builds_once_under_concurrency(tmp_path):
    prepared = _prepared(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    sync_calls = 0

    def run(command: list[str]) -> None:
        nonlocal sync_calls
        if command[1:3] != ["pip", "install"]:
            return
        sync_calls += 1
        entered.set()
        assert release.wait(timeout=5)
        _install_fake_environment(tmp_path, prepared)

    def ensure():
        return _ensure(tmp_path, prepared, run)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(ensure)
        assert entered.wait(timeout=5)
        second = executor.submit(ensure)
        release.set()
        assert sorted((first.result(timeout=5), second.result(timeout=5))) == [False, True]
    assert sync_calls == 1


def test_different_environments_do_not_share_a_build_lock(tmp_path):
    first = _prepared(tmp_path, environment_hash="first")
    second = _prepared(tmp_path, environment_hash="second")
    barrier = threading.Barrier(2)

    def ensure(prepared):
        def run(command: list[str]) -> None:
            if command[1:3] == ["pip", "install"]:
                barrier.wait(timeout=5)
                _install_fake_environment(tmp_path, prepared)

        return _ensure(tmp_path, prepared, run)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(ensure, (first, second)))
    assert results == (True, True)


def test_environment_commands_use_exact_synchronization(tmp_path):
    lockfile = tmp_path / "lock.txt"
    lockfile.write_text("ddtrace==2.20.1\nalpha==1.0\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'example'\nversion = '1.0'\n")
    prepared = prepare_environment(
        tmp_path,
        environment_hash="example",
        lockfile=lockfile.relative_to(tmp_path),
    )

    commands = environment_commands(
        prepared,
        python=PYTHON,
        exclude_newer="2026-01-01T00:00:00Z",
    )
    requirements = (tmp_path / prepared.requirements).read_text()
    assert commands[1][0:3] == ["uv", "pip", "install"]
    assert "--exact" in commands[1]
    assert "--config-setting" not in commands[1]
    assert commands[1][-2:] == ["--config-settings-package", "ddtrace:editable_mode=compat"]
    assert "ddtrace==2.20.1" not in requirements
    assert "-e ." in requirements


def test_environment_commands_install_prebuilt_editable_artifact(tmp_path):
    lockfile = tmp_path / "lock.txt"
    lockfile.write_text("ddtrace==2.20.1\nalpha==1.0\n")
    wheel = tmp_path / "ddtrace-4.15.0-0.editable-cp311-cp311-linux_x86_64.whl"
    wheel.touch()
    prepared = prepare_environment(
        tmp_path,
        environment_hash="example",
        lockfile=lockfile.relative_to(tmp_path),
        project_artifact=wheel.relative_to(tmp_path),
    )

    commands = environment_commands(prepared, python=PYTHON)
    requirements = (tmp_path / prepared.requirements).read_text()
    assert commands[1][0:3] == ["uv", "pip", "install"]
    assert "--config-settings-package" not in commands[1]
    assert "ddtrace==2.20.1" not in requirements
    assert "-e ." not in requirements
    assert str(wheel) in requirements


def test_project_artifact_must_exist(tmp_path):
    lockfile = tmp_path / "lock.txt"
    lockfile.write_text("alpha==1.0\n")

    with pytest.raises(UvTestEnvironmentError, match="project artifact does not exist"):
        prepare_environment(
            tmp_path,
            environment_hash="example",
            lockfile=lockfile.relative_to(tmp_path),
            project_artifact=Path("missing.whl"),
        )
