"""
Test the global configuration of the library.

Settings should have a well-defined precedence.
"""
import pytest

from ddtrace import Tracer
from ddtrace import config
from ddtrace.sampler import SamplingRule


# Reset the configuration between each test case.
# This is necessary because the config object is a singleton.
@pytest.fixture(autouse=True)
def reset_config():
    config.reset()
    yield
    config.reset()


@pytest.mark.parametrize(
    "test",
    [
        {
            "expected_service": None,
        },
        {
            "env": {"DD_SERVICE": "env_svc"},
            "expected_service": "env_svc",
        },
        {
            "env": {"DD_TAGS": "service:env_svc"},
            "expected_service": "env_svc",
        },
        {
            "code": "code_svc",
            "expected_service": "code_svc",
        },
        {
            "env": {"DD_SERVICE": "env_svc1", "DD_TAGS": "service:env_svc2"},
            "expected_service": "env_svc2",
        },
        {
            "env": {"DD_SERVICE": "env_svc1", "DD_TAGS": "service:env_svc2"},
            "code": "code_svc",
            "expected_service": "code_svc",
        },
    ],
)
def test_service(test, tracer, monkeypatch):
    for env_name, env_value in test.get("env", {}).items():
        monkeypatch.setenv(env_name, env_value)
        # Need a new tracer to pick up the new env vars.
        tracer = Tracer()
    if "code" in test:
        config.service = test["code"]
    assert config.service == test["expected_service"]

    # Ensure the service is used on traces.
    with tracer.trace("") as span:
        pass

    assert span.service == test["expected_service"]


@pytest.mark.parametrize(
    "test",
    [
        {
            # Default case
            "trace_service": "svc",
            "expected_service_mapping": {},
            "expected_service": "svc",
        },
        {
            "env": {"DD_SERVICE_MAPPING": "svc:env_svc"},
            "trace_service": "svc",
            "expected_service_mapping": {"svc": "env_svc"},
            "expected_service": "svc",
        },
        {
            "code": {"svc": "code_svc"},
            "trace_service": "svc",
            "expected_service_mapping": {"svc": "code_svc"},
            "expected_service": "code_svc",
        },
        {
            "env": {"DD_SERVICE_MAPPING": "svc:env_svc"},
            "code": {"svc": "code_svc"},
            "trace_service": "svc",
            "expected_service_mapping": {"svc": "code_svc"},
            "expected_service": "code_svc",
        },
    ],
)
def test_service_mapping(test, tracer, monkeypatch):
    for env_name, env_value in test.get("env", {}).items():
        monkeypatch.setenv(env_name, env_value)
    if "code" in test:
        config.service_mapping = test["code"]

    assert config.service_mapping == test["expected_service_mapping"]

    trace_kwargs = {"service": test["trace_service"]} if "trace_service" in test else {}
    with tracer.trace("", **trace_kwargs) as span:
        pass
    assert span.service == test["expected_service"]


@pytest.mark.parametrize(
    "test",
    [
        {
            # Defaults case
            "expected_sample_rate": 1.0,
            "expected_rate_limit": 100,
            "expected_rules": [],
        },
        {
            # Env case
            "env": {
                "DD_TRACE_SAMPLE_RATE": "0.2",
                "DD_TRACE_RATE_LIMIT": "20",
                "DD_TRACE_SAMPLING_RULES": '[{"service": "abc","sample_rate": 0.5,"name":"op_name"}]',
            },
            "expected_sample_rate": 0.2,
            "expected_rate_limit": 20,
            "expected_rules": [SamplingRule(sample_rate=0.5, service="abc", name="op_name")],
        },
        {
            # Code case
            "code": {
                "trace_sample_rate": 0.3,
                "trace_rate_limit": 30,
                "trace_sampling_rules": [SamplingRule(sample_rate=0.6, service="abc", name="op_name")],
            },
            "expected_sample_rate": 0.3,
            "expected_rate_limit": 30,
            "expected_rules": [SamplingRule(sample_rate=0.6, service="abc", name="op_name")],
        },
        {
            # Env and code case (code should take priority)
            "env": {
                "DD_TRACE_SAMPLE_RATE": "0.2",
                "DD_TRACE_RATE_LIMIT": "20",
                "DD_TRACE_SAMPLING_RULES": '[{"service": "abc","sample_rate": 0.5,"name":"op_name"}]',
            },
            "code": {
                "trace_sample_rate": 0.3,
                "trace_rate_limit": 30,
                "trace_sampling_rules": [SamplingRule(sample_rate=0.6, service="abc", name="op_name")],
            },
            "expected_sample_rate": 0.3,
            "expected_rate_limit": 30,
            "expected_rules": [SamplingRule(sample_rate=0.6, service="abc", name="op_name")],
        },
        {
            # DD_TRACE_SAMPLE_RATE and DD_TRACE_RATE_LIMIT should be used when not provided via code.
            "env": {"DD_TRACE_SAMPLE_RATE": "0.2", "DD_TRACE_RATE_LIMIT": "20"},
            "code": {
                "trace_sampling_rules": [SamplingRule(sample_rate=0.6, service="abc", name="op_name")],
            },
            "expected_sample_rate": 0.2,
            "expected_rate_limit": 20,
            "expected_rules": [SamplingRule(sample_rate=0.6, service="abc", name="op_name")],
        },
    ],
)
def test_trace_sampling(test, monkeypatch):
    """Ensure that trace sampling can be configured via environment or code."""
    tracer = Tracer()

    # Set and test the env settings
    for env_name, env_value in test.get("env", {}).items():
        monkeypatch.setenv(env_name, env_value)
        # Have to reset the tracer to pick up the new env var since samplers
        # are initialized when the tracer is.
        tracer = Tracer()

    # Set and test the code settings
    for setting_name, setting_value in test.get("code", {}).items():
        if setting_name == "trace_sample_rate":
            config.trace_sample_rate = setting_value
        if setting_name == "trace_sampling_rules":
            config.trace_sampling_rules = setting_value
        if setting_name == "trace_rate_limit":
            config.trace_rate_limit = setting_value

    # Expect 1 rule each time for the default settings
    assert len(tracer._config.sampler.rules) == 1 + len(test["expected_rules"])
    assert tracer._config.sampler.limiter.rate_limit == test["expected_rate_limit"]

    # The last sampling rule is the default rule that uses the global sampling rate / limit.
    last_sampling_rule = tracer._config.sampler.rules[-1]
    assert last_sampling_rule.service is SamplingRule.NO_RULE
    assert last_sampling_rule.sample_rate == test["expected_sample_rate"]

    for r1, r2 in zip(tracer._config.sampler.rules[:-1], test["expected_rules"]):
        assert r1.sample_rate == r2.sample_rate
        assert r1.service == r2.service
        assert r1.name == r2.name
