import pytest
import ray.dashboard.modules.job.job_manager  # noqa: F401

from tests.contrib.patch_scenarios import PATCH_SCENARIOS


def test_ml_job_env_parses_key_value_pairs():
    """DD_ML_JOB_ENV semicolon-separated pairs are forwarded verbatim as KEY=VALUE."""
    from ddtrace.contrib.internal.ray.patch import _parse_ml_job_env

    result = _parse_ml_job_env("DD_TRACE_ENABLED:true;DD_AGENT_HOST:10.0.0.1")
    assert result == {"DD_TRACE_ENABLED": "true", "DD_AGENT_HOST": "10.0.0.1"}


def test_ml_job_env_value_may_contain_colons():
    """Only the first colon splits key from value; subsequent colons stay in the value."""
    from ddtrace.contrib.internal.ray.patch import _parse_ml_job_env

    result = _parse_ml_job_env("DD_AGENT_URL:http://localhost:8126")
    assert result == {"DD_AGENT_URL": "http://localhost:8126"}


def test_ml_job_env_strips_whitespace():
    """Leading/trailing whitespace around keys and values is stripped."""
    from ddtrace.contrib.internal.ray.patch import _parse_ml_job_env

    result = _parse_ml_job_env(" DD_TRACE_ENABLED : true ; DD_AGENT_HOST : myhost ")
    assert result == {"DD_TRACE_ENABLED": "true", "DD_AGENT_HOST": "myhost"}


def test_ml_job_env_skips_malformed_pairs():
    """Pairs without a colon separator are silently skipped."""
    from ddtrace.contrib.internal.ray.patch import _parse_ml_job_env

    result = _parse_ml_job_env("MY_KEY:val;no_separator;ANOTHER:ok")
    assert result == {"MY_KEY": "val", "ANOTHER": "ok"}


def test_ml_job_env_empty_string_returns_empty():
    from ddtrace.contrib.internal.ray.patch import _parse_ml_job_env

    assert _parse_ml_job_env("") == {}


def test_ml_job_env_setdefault_does_not_override_explicit(monkeypatch):
    """Values already in env_vars are not overridden by DD_ML_JOB_ENV."""
    from ddtrace.contrib.internal.ray.patch import _parse_ml_job_env

    env_vars = {"DD_TRACE_ENABLED": "true"}
    for k, v in _parse_ml_job_env("DD_TRACE_ENABLED:false").items():
        env_vars.setdefault(k, v)

    assert env_vars["DD_TRACE_ENABLED"] == "true"


@pytest.mark.parametrize("scenario", PATCH_SCENARIOS)
def test_TestRayPatch(python_process, scenario):
    module = "tests.contrib.ray"
    module += "._test_ray_patch_scenarios"
    case = "TestRayPatch"
    result = python_process(
        f"from tests.contrib.patch import run_scenario; run_scenario({module!r}, {case!r}, {scenario!r})"
    )
    if result.returncode == 77:
        pytest.skip(result.stdout.decode())
    if result.returncode == 78:
        pytest.xfail(result.stdout.decode())
    result.check_returncode()
