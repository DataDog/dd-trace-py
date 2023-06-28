import pytest

from ddtrace import Tracer
from ddtrace import config
from ddtrace.sampler import DatadogSampler
from ddtrace.sampler import SamplingRule


@pytest.fixture(autouse=True)
def reset_config():
    config.reset()
    yield
    config.reset()


def _base_config(cfg):
    lib_config = {
        "runtime_metrics_enabled": None,
        "tracing_debug": None,
        "tracing_http_header_tags": None,
        "tracing_service_mapping": None,
        "tracing_sampling_rate": None,
        "tracing_sampling_rules": None,
        "span_sampling_rules": None,
        "data_streams_enabled": None,
    }
    lib_config.update(cfg)

    return {
        "action": "enable",
        "service_target": {"service": None, "env": None},
        "lib_config": lib_config,
    }


@pytest.mark.parametrize(
    "config_sources",
    [
        # Default case, assert the default sampling configuration
        {},
        {
            "env": {"DD_TRACE_SAMPLE_RATE": "0.25"},
            "code": {"trace_sample_rate": "0.25"},
            "rc": {"tracing_sample_rate": "0.25"},
        },
        # {
        #     "env": {"DD_TRACE_SAMPLE_RATE": "0.25"},
        #     "code": {"trace_sample_rate": "0.25"},
        #     "rc": {"tracing_sample_rate": "0.25"},
        # },
    ],
)
def test_sample_rate(config_sources, tracer, monkeypatch):
    if "env" in config_sources:
        for env_name, env_value in config_sources.get("env", {}).items():
            monkeypatch.setenv(env_name, env_value)

        monkeypatch.setenv("DD_TRACE_SAMPLE_RATE", "0.25")
        assert config.trace_sample_rate == 0.25
        assert config._resolved_item("trace_sample_rate") == {
            "source": "DD_TRACE_SAMPLE_RATE",
            "value": 0.25,
            "raw": "0.25",
        }

    if "code" in config_sources:
        config.service_mapping = {"svc": "code_svc"}
        assert config._resolved_item("service_mapping") == {
            "source": "user",
            "value": {"svc": "code_svc"},
            "raw": {"svc": "code_svc"},
        }

    if "env" not in config_sources and "code" not in config_sources:
        assert config._resolved_item("service_mapping") == {
            "source": "default",
            "value": {},
            "raw": {},
        }
        assert config.service_mapping == {}

    with tracer.trace("before_config", service="svc") as span:
        pass

    # User code should take precedence over env.
    if "code" in config_sources:
        assert span.service == "code_svc"
    elif "env" in config_sources:
        assert span.service == "env_svc"
    else:
        assert span.service == "svc"

    if "rc" in config_sources:
        config._update_rc(None, _base_config({"tracing_service_mapping": [{"from_key": "svc", "to_name": "rc_svc"}]}))

    with tracer.trace("after_config", service="svc") as span:
        pass

    if "rc" in config_sources:
        assert span.service == "rc_svc"
    elif "code" in config_sources:
        assert span.service == "code_svc"
    elif "env" in config_sources:
        assert span.service == "env_svc"
    else:
        assert span.service == "svc"

    # Unset the remote config to ensure it is reverted.
    if "rc" in config_sources:
        config._update_rc(None, _base_config({"tracing_service_mapping": None}))

    with tracer.trace("after_config", service="svc") as span:
        pass

    if "code" in config_sources:
        assert span.service == "code_svc"
    elif "env" in config_sources:
        assert span.service == "env_svc"
    else:
        assert span.service == "svc"


@pytest.mark.parametrize(
    "test",
    [
        {
            # Defaults case
            "expected_rules": [],
        },
        {
            "env": {"DD_TRACE_SAMPLE_RATE": "0.2"},
            "expected_rules": [SamplingRule(sample_rate=0.2, service=SamplingRule.NO_RULE, name=SamplingRule.NO_RULE)],
        },
        {
            # Env case
            "env": {
                "DD_TRACE_SAMPLE_RATE": "0.2",
                "DD_TRACE_SAMPLING_RULES": '[{"service": "abc","sample_rate": 0.5,"name":"op_name"}]',
            },
            "expected_rules": [
                SamplingRule(sample_rate=0.5, service="abc", name="op_name"),
                SamplingRule(sample_rate=0.2, service=SamplingRule.NO_RULE, name=SamplingRule.NO_RULE),
            ],
        },
        {
            # Code case
            "code": {
                "trace_sample_rate": 0.3,
                "trace_sampling_rules": [SamplingRule(sample_rate=0.6, service="abc", name="op_name")],
            },
            "expected_rules": [
                SamplingRule(sample_rate=0.6, service="abc", name="op_name"),
                SamplingRule(sample_rate=0.3, service=SamplingRule.NO_RULE, name=SamplingRule.NO_RULE),
            ],
        },
        {
            "env": {"DD_TRACE_SAMPLE_RATE": "0.2"},
            "code": {
                "trace_sampling_rules": [SamplingRule(sample_rate=0.6, service="abc", name="op_name")],
            },
            "expected_rules": [
                SamplingRule(sample_rate=0.6, service="abc", name="op_name"),
                SamplingRule(sample_rate=0.2, service=SamplingRule.NO_RULE, name=SamplingRule.NO_RULE),
            ],
        },
        {
            # Env and code case (code should take priority)
            "env": {
                "DD_TRACE_SAMPLE_RATE": "0.2",
                "DD_TRACE_SAMPLING_RULES": '[{"service": "abc","sample_rate": 0.5,"name":"op_name"}]',
            },
            "code": {
                "trace_sample_rate": 0.3,
                "trace_sampling_rules": [SamplingRule(sample_rate=0.6, service="abc", name="op_name")],
            },
            "expected_rules": [
                SamplingRule(sample_rate=0.6, service="abc", name="op_name"),
                SamplingRule(sample_rate=0.3, service=SamplingRule.NO_RULE, name=SamplingRule.NO_RULE),
            ],
        },
        {
            # Remote config should override environment settings
            "env": {"DD_TRACE_SAMPLE_RATE": "0.2"},
            "rc": {"tracing_sampling_rate": 0.1},
            "expected_rules": [SamplingRule(sample_rate=0.1, service=SamplingRule.NO_RULE, name=SamplingRule.NO_RULE)],
            "expected_rules_unset": [
                SamplingRule(sample_rate=0.2, service=SamplingRule.NO_RULE, name=SamplingRule.NO_RULE)
            ],
        },
        {
            # Remote config should override all settings
            "env": {"DD_TRACE_SAMPLE_RATE": "0.2"},
            "code": {
                "trace_sampling_rules": [SamplingRule(sample_rate=0.6, service="abc", name="op_name")],
            },
            "rc": {
                "tracing_sampling_rate": 0.1,
            },
            "expected_rules": [
                SamplingRule(sample_rate=0.6, service="abc", name="op_name"),
                SamplingRule(sample_rate=0.1, service=SamplingRule.NO_RULE, name=SamplingRule.NO_RULE),
            ],
            "expected_rules_unset": [
                SamplingRule(sample_rate=0.6, service="abc", name="op_name"),
                SamplingRule(sample_rate=0.2, service=SamplingRule.NO_RULE, name=SamplingRule.NO_RULE),
            ],
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
    if "code" in test:
        tracer.configure(
            sampler=DatadogSampler(
                default_sample_rate=test["code"].get("trace_sample_rate"),
                rate_limit=test["code"].get("trace_rate_limit"),
                rules=test["code"].get("trace_sampling_rules"),
            )
        )

    if "rc" in test:
        config._update_rc(
            None,
            _base_config(
                {
                    "tracing_sampling_rate": test["rc"].get("tracing_sampling_rate"),
                }
            ),
        )

    assert len(tracer._config.sampler.rules) == len(test["expected_rules"])
    for actual, expected in zip(tracer._config.sampler.rules, test["expected_rules"]):
        assert actual.sample_rate == expected.sample_rate
        assert actual.service == expected.service
        assert actual.name == expected.name

    # Test unsetting RC
    if "rc" not in test:
        return

    config._update_rc(
        None,
        _base_config(
            {
                "tracing_sampling_rate": None,
            }
        ),
    )

    assert len(tracer._config.sampler.rules) == len(test["expected_rules_unset"])
    for actual, expected in zip(tracer._config.sampler.rules, test["expected_rules_unset"]):
        assert actual.sample_rate == expected.sample_rate
        assert actual.service == expected.service
        assert actual.name == expected.name


def test_trace_sampling_set_unset(monkeypatch):
    monkeypatch.setenv("DD_TRACE_SAMPLE_RATE", "0.2")
    tracer = Tracer()
    config._update_rc(None, _base_config({"tracing_sampling_rate": 0.3}))
    assert len(tracer._config.sampler.rules) == 1
    assert tracer._config.sampler.rules[0].sample_rate == 0.3
    assert tracer._config.sampler.rules[0].service == SamplingRule.NO_RULE
    assert tracer._config.sampler.rules[0].name == SamplingRule.NO_RULE

    config._update_rc(None, _base_config({"tracing_sampling_rate": None}))
    assert len(tracer._config.sampler.rules) == 1
    assert tracer._config.sampler.rules[0].sample_rate == 0.2
    assert tracer._config.sampler.rules[0].service == SamplingRule.NO_RULE
    assert tracer._config.sampler.rules[0].name == SamplingRule.NO_RULE


def test_trace_sampling_user(monkeypatch):
    tracer = Tracer()
    tracer.configure(
        sampler=DatadogSampler(
            default_sample_rate=0.2,
            rules=[SamplingRule(sample_rate=0.6, service="abc", name="op_name")],
        )
    )
    config._update_rc(None, _base_config({"tracing_sampling_rate": 0.3}))
    assert len(tracer._config.sampler.rules) == 2
    assert tracer._config.sampler.rules[0].sample_rate == 0.6
    assert tracer._config.sampler.rules[0].service == "abc"
    assert tracer._config.sampler.rules[0].name == "op_name"
    assert tracer._config.sampler.rules[1].sample_rate == 0.3
    assert tracer._config.sampler.rules[1].service == SamplingRule.NO_RULE
    assert tracer._config.sampler.rules[1].name == SamplingRule.NO_RULE

    # Unset RC
    config._update_rc(None, _base_config({"tracing_sampling_rate": None}))
    assert len(tracer._config.sampler.rules) == 2
    assert tracer._config.sampler.rules[0].sample_rate == 0.6
    assert tracer._config.sampler.rules[0].service == "abc"
    assert tracer._config.sampler.rules[0].name == "op_name"
    assert tracer._config.sampler.rules[1].sample_rate == 0.2
    assert tracer._config.sampler.rules[1].service == SamplingRule.NO_RULE
    assert tracer._config.sampler.rules[1].name == SamplingRule.NO_RULE
