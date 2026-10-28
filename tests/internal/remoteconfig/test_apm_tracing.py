from ddtrace import config
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
