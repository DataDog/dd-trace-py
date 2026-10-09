from ddtrace import config
from ddtrace.internal.remoteconfig import ConfigMetadata
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.remoteconfig.products.apm_tracing import APMTracingCallback
from tests.utils import remote_config_build_payload as build_payload


def test_env_target_applied_when_config_env_is_empty(monkeypatch):
    payload = build_payload(
        "APM_TRACING",
        {
            "service_target": {"service": "*", "env": "agent-env"},
            "lib_config": {"tracing_enabled": True},
        },
        "config",
    )

    monkeypatch.setattr(config, "env", "")
    chained_config = APMTracingCallback()._process_payloads([payload])

    assert chained_config["tracing_enabled"] is True


def _apm_tracing_payload(config_id, service, lib_config):
    return Payload(
        ConfigMetadata(id=config_id, product_name="APM_TRACING", sha256_hash=None, length=None, tuf_version=1),
        f"Datadog/1/APM_TRACING/{config_id}/config",
        {"service_target": {"service": service, "env": "*"}, "lib_config": lib_config},
    )


def _removal(config_id):
    return Payload(
        ConfigMetadata(id=config_id, product_name="APM_TRACING", sha256_hash=None, length=None, tuf_version=2),
        f"Datadog/1/APM_TRACING/{config_id}/config",
        None,
    )


def test_config_survives_dispatch_of_unrelated_config(monkeypatch):
    monkeypatch.setattr(config, "service", "my-service")
    rules = [{"service": "my-service", "sample_rate": 0.5, "provenance": "dynamic"}]
    callback = APMTracingCallback()

    callback._process_payloads([_apm_tracing_payload("mine", "my-service", {"tracing_sampling_rules": rules})])
    # The client only dispatches what changed, e.g. a config targeting another service.
    chained_config = callback._process_payloads(
        [_apm_tracing_payload("other", "other-service", {"tracing_sampling_rules": []})]
    )

    assert chained_config["tracing_sampling_rules"] == rules


def test_config_removal_is_applied(monkeypatch):
    monkeypatch.setattr(config, "service", "my-service")
    callback = APMTracingCallback()
    callback._process_payloads(
        [_apm_tracing_payload("mine", "my-service", {"tracing_sampling_rules": [{"sample_rate": 0.5}]})]
    )

    chained_config = callback._process_payloads([_removal("mine")])

    assert "tracing_sampling_rules" not in chained_config


def test_config_retargeted_to_another_service_is_dropped(monkeypatch):
    monkeypatch.setattr(config, "service", "my-service")
    callback = APMTracingCallback()
    callback._process_payloads(
        [_apm_tracing_payload("mine", "my-service", {"tracing_sampling_rules": [{"sample_rate": 0.5}]})]
    )

    chained_config = callback._process_payloads(
        [_apm_tracing_payload("mine", "other-service", {"tracing_sampling_rules": [{"sample_rate": 0.5}]})]
    )

    assert "tracing_sampling_rules" not in chained_config
