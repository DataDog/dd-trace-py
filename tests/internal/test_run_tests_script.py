import importlib.machinery
import importlib.util
from pathlib import Path
import types
from unittest import mock

import pytest


_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "run-tests"
_MATRIX_DEFAULTS = {"env": {"CMAKE_BUILD_PARALLEL_LEVEL": "12"}}
_SUBPROCESS_CONFIG = {
    "runner": "uv",
    "pattern": "^subprocess$",
    "matrix": {
        "python": ["3.9", "3.10", "3.11", "3.12", "3.13", "3.14"],
        "command": "pytest -vvvv {cmdargs} --no-cov tests/contrib/subprocess",
        "dependencies": ["pytest-randomly"],
    },
}


@pytest.fixture(scope="module")
def run_tests_script():
    riot_adapter = types.ModuleType("tests.riot_adapter")
    riot_adapter.load_riot_test_environments = lambda suites: {}
    suitespec = types.ModuleType("tests.suitespec")
    suitespec.get_matrix_defaults = lambda: _MATRIX_DEFAULTS
    suitespec.get_patterns = lambda suite: set()
    suitespec.get_suites = lambda: {"contrib::subprocess": _SUBPROCESS_CONFIG}

    loader = importlib.machinery.SourceFileLoader("run_tests_script", str(_SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(
        "sys.modules",
        {
            "tests.riot_adapter": riot_adapter,
            "tests.suitespec": suitespec,
        },
    ):
        loader.exec_module(module)
    return module


def _subprocess_environment(run_tests_script, python="3.12"):
    runner = run_tests_script.TestRunner()
    environments = runner.get_test_environments(
        _SUBPROCESS_CONFIG["pattern"],
        suite_name="contrib::subprocess",
        suite_config=_SUBPROCESS_CONFIG,
    )
    return runner, next(environment for environment in environments if environment.python == python)


def test_uv_canary_uses_descriptive_environment_ids(run_tests_script):
    runner, _ = _subprocess_environment(run_tests_script)

    environments = runner.get_test_environments(
        _SUBPROCESS_CONFIG["pattern"],
        suite_name="contrib::subprocess",
        suite_config=_SUBPROCESS_CONFIG,
    )

    assert [environment.id for environment in environments] == [
        "subprocess-py39",
        "subprocess-py310",
        "subprocess-py311",
        "subprocess-py312",
        "subprocess-py313",
        "subprocess-py314",
    ]
    assert all(environment.lockfile.name == f"{environment.id}.txt" for environment in environments)


def test_uv_build_commands_install_descriptive_uv_lock(run_tests_script, monkeypatch):
    runner, environment = _subprocess_environment(run_tests_script)
    monkeypatch.setattr(run_tests_script, "cooldown_cutoff", lambda: "2026-08-18T12:00:00Z")

    commands = runner._uv_build_commands(environment, {"CMAKE_BUILD_PARALLEL_LEVEL": "12"})

    assert commands[0][commands[0].index("uv") :] == [
        "uv",
        "venv",
        "--allow-existing",
        "--python",
        "3.12",
        "--no-python-downloads",
        ".cache/uv-test-environments/contrib/subprocess/subprocess-py312",
    ]
    sync = commands[1]
    lockfile = "tests/locks/contrib/subprocess/subprocess-py312.txt"
    assert sync[sync.index("--python") + 2] == lockfile
    assert "--requirements" not in sync
    install = commands[2]
    assert install[install.index("--exclude-newer") + 1] == "2026-08-18T12:00:00Z"
    assert "--editable" in install
    assert all("CMAKE_BUILD_PARALLEL_LEVEL=12" in command for command in commands)


def test_uv_test_command_uses_environment_executable_and_run_environment(run_tests_script):
    runner, environment = _subprocess_environment(run_tests_script)

    command = runner._uv_test_command(
        environment,
        environment.runs[0],
        ["-k", "selected"],
        {"SUITE_SETTING": "enabled"},
    )

    assert "SUITE_SETTING=enabled" in command
    assert any(argument.startswith("CMAKE_BUILD_PARALLEL_LEVEL=") for argument in command)
    assert (
        "PATH=/home/bits/project/.cache/uv-test-environments/contrib/subprocess/"
        "subprocess-py312/bin:/home/bits/.cargo/bin:/home/bits/.local/bin:/home/bits/.pyenv/shims:"
        "/home/bits/.pyenv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    ) in command
    assert "PYTHONPATH=/home/bits/project" in command
    assert "VIRTUAL_ENV=/home/bits/project/.cache/uv-test-environments/contrib/subprocess/subprocess-py312" in command
    assert command[-6:] == [
        ".cache/uv-test-environments/contrib/subprocess/subprocess-py312/bin/pytest",
        "-vvvv",
        "-k",
        "selected",
        "--no-cov",
        "tests/contrib/subprocess",
    ]


def test_uv_build_receives_matrix_environment(run_tests_script, monkeypatch):
    runner, environment = _subprocess_environment(run_tests_script)
    captured = {}

    def build_commands(_, forwarded_env):
        captured.update(forwarded_env)
        return (["true"],)

    monkeypatch.setattr(runner, "_uv_build_commands", build_commands)

    assert runner._run_uv_suite([environment], {"SUITE_SETTING": "enabled"}, [], dry_run=True)
    assert captured["SUITE_SETTING"] == "enabled"
    assert "CMAKE_BUILD_PARALLEL_LEVEL" in captured


def test_uv_commands_execute_directly_in_gitlab_ci(run_tests_script, monkeypatch):
    monkeypatch.setenv("GITLAB_CI", "true")
    runner, environment = _subprocess_environment(run_tests_script)

    command = runner._uv_test_command(environment, environment.runs[0], [], {})

    assert command[0] == "env"
    environment_bin = str(_ROOT / ".cache/uv-test-environments/contrib/subprocess/subprocess-py312/bin")
    assert any(argument.startswith(f"PATH={environment_bin}:") for argument in command)
    assert any(argument.startswith(f"PYTHONPATH={_ROOT}") for argument in command)
    assert str(_ROOT / ".cache/uv-test-environments/contrib/subprocess/subprocess-py312/bin/pytest") in command
