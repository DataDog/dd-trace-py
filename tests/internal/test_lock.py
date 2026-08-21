import datetime as dt
from pathlib import Path
import subprocess

import pytest
import yaml

from tests.environment import LOCK_ROOT
from tests.environment import TestEnvironment as Environment
from tests.environment import lockfile_path
from tests.lock import LockError
from tests.lock import compile_environment
from tests.lock import cooldown_cutoff
from tests.lock import generate_locks
from tests.lock import select_environments
from tests.matrix import expand_declared_matrices


_ROOT = Path(__file__).parents[2]


def _suite(command="pytest tests/example"):
    return {
        "matrix": {
            "python": ["3.11"],
            "command": command,
            "dependencies": ["pytest", "example<2"],
        }
    }


def _fake_uv(command, **kwargs):
    requirements = Path(command[-1]).read_text()
    output = Path(command[command.index("--output-file") + 1])
    output.write_text("example==1.0.0\npytest==8.0.0\n")
    return subprocess.CompletedProcess(command, 0, requirements, "")


def test_select_environments_accepts_short_and_full_suite_names():
    suites = {"contrib::example": _suite(), "tracer": _suite("pytest tests/tracer")}

    short, short_suites = select_environments(suites, {}, ["example"])
    full, full_suites = select_environments(suites, {}, ["contrib::example"])

    assert short == full
    assert short_suites == full_suites == ("contrib::example",)
    assert short[0].lockfile == Path("tests/locks/contrib/example/example-py311.txt")
    assert short[0].platform == "linux"


def test_lockfile_path_is_safe_for_subsuites():
    path = lockfile_path("ci_visibility::pytest:snapshot", "pytest-snapshot-py312")

    assert path == Path("tests/locks/ci_visibility/pytest-snapshot/pytest-snapshot-py312.txt")
    assert ":" not in path.as_posix()


def test_select_environments_rejects_unknown_suites():
    with pytest.raises(LockError, match="has no declarative matrix"):
        select_environments({"contrib::example": _suite()}, {}, ["missing"])


def test_compile_environment_targets_concrete_python_and_platform(tmp_path):
    calls = []

    def fake_uv(command, **kwargs):
        calls.append((command, kwargs, Path(command[-1]).read_text()))
        return _fake_uv(command, **kwargs)

    environment = Environment(
        id="example-py311",
        suite="contrib::example",
        name="example",
        python="3.11",
        platform="x86_64-manylinux2014",
        direct_dependencies=("pytest", "example<2"),
        lockfile=lockfile_path("contrib::example", "example-py311"),
    )

    content = compile_environment(environment, root=tmp_path, exclude_newer="2026-08-18T12:00:00Z", run=fake_uv)

    command, kwargs, requirements = calls[0]
    assert command[:3] == ["uv", "pip", "compile"]
    assert command[command.index("--python-version") + 1] == "3.11"
    assert command[command.index("--python-platform") + 1] == "x86_64-manylinux2014"
    assert command[command.index("--exclude-newer") + 1] == "2026-08-18T12:00:00Z"
    assert {"--no-annotate", "--no-header", "--no-python-downloads", "--no-sources"} <= set(command)
    assert requirements == "example<2\npytest\n"
    assert kwargs == {"cwd": tmp_path, "check": True, "text": True, "capture_output": True}
    assert content == "example==1.0.0\npytest==8.0.0\n"


def test_cooldown_cutoff_is_48_hours_in_utc():
    now = dt.datetime(2026, 8, 20, 14, 30, 45, 123456, tzinfo=dt.timezone(dt.timedelta(hours=-4)))

    assert cooldown_cutoff(now) == "2026-08-18T18:30:45Z"


def test_cooldown_cutoff_rejects_naive_timestamps():
    with pytest.raises(LockError, match="timezone-aware"):
        cooldown_cutoff(dt.datetime(2026, 8, 20, 12, 0, 0))


def test_generate_locks_prunes_only_selected_suite(tmp_path):
    obsolete = tmp_path / "tests/locks/contrib/example/obsolete.txt"
    unrelated = tmp_path / "tests/locks/tracer/obsolete.txt"
    obsolete.parent.mkdir(parents=True)
    unrelated.parent.mkdir(parents=True)
    obsolete.write_text("old==1\n")
    unrelated.write_text("old==1\n")

    written, pruned = generate_locks(
        {"contrib::example": _suite(), "tracer": _suite()},
        {},
        ["example"],
        root=tmp_path,
        jobs=2,
        run=_fake_uv,
    )

    assert written == (Path("tests/locks/contrib/example/example-py311.txt"),)
    assert pruned == (Path("tests/locks/contrib/example/obsolete.txt"),)
    assert (tmp_path / written[0]).read_text() == "example==1.0.0\npytest==8.0.0\n"
    assert unrelated.exists()


def test_generate_locks_compiles_all_selected_environments(tmp_path):
    suites = {
        "contrib::example": _suite(),
        "tracer": _suite("pytest tests/tracer"),
    }

    written, _ = generate_locks(
        suites,
        {},
        root=tmp_path,
        run=_fake_uv,
    )

    assert written == (
        Path("tests/locks/contrib/example/example-py311.txt"),
        Path("tests/locks/tracer/tracer-py311.txt"),
    )
    assert (tmp_path / written[0]).read_text() == "example==1.0.0\npytest==8.0.0\n"
    assert (tmp_path / written[1]).read_text() == "example==1.0.0\npytest==8.0.0\n"


def test_generate_locks_does_not_modify_existing_locks_on_compile_failure(tmp_path):
    lockfile = tmp_path / "tests/locks/contrib/example/example-py311.txt"
    lockfile.parent.mkdir(parents=True)
    lockfile.write_text("existing==1\n")

    def failed_uv(command, **kwargs):
        raise subprocess.CalledProcessError(1, command, stderr="resolution failed")

    with pytest.raises(LockError, match="resolution failed"):
        generate_locks(
            {"contrib::example": _suite()},
            {},
            ["example"],
            root=tmp_path,
            run=failed_uv,
        )

    assert lockfile.read_text() == "existing==1\n"


def test_compile_environment_reports_resolution_failure(tmp_path):
    environment = select_environments({"contrib::example": _suite()}, {}, ["example"])[0][0]

    def failed_uv(command, **kwargs):
        raise subprocess.CalledProcessError(1, command, stderr="resolution failed")

    with pytest.raises(LockError, match="resolution failed"):
        compile_environment(environment, root=tmp_path, run=failed_uv)


def test_generated_locks_cover_every_declared_environment():
    suites = {}
    defaults = {}
    for search_root, prefix in ((_ROOT / "tests", ""), (_ROOT / "benchmarks", "benchmarks")):
        for specfile in search_root.rglob("suitespec.yml"):
            data = yaml.safe_load(specfile.read_text())
            defaults.update(data.get("matrix_defaults", {}))
            namespace_parts = specfile.relative_to(search_root).parts[:-1]
            namespace = "::".join(namespace_parts) if namespace_parts else prefix
            for name, config in data.get("suites", {}).items():
                suites[f"{namespace}::{name}" if namespace else name] = config

    environments = expand_declared_matrices(suites, defaults, nightly=False)
    expected = {
        environment.lockfile for suite_environments in environments.values() for environment in suite_environments
    }
    actual = {path.relative_to(_ROOT) for path in (_ROOT / LOCK_ROOT).rglob("*.txt")}

    assert None not in expected
    assert actual == expected
    assert max((_ROOT / path).stat().st_size for path in actual) < 128 * 1024
