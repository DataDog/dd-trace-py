from ddtrace import config
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.remoteconfig.products.apm_tracing import APMTracingCallback
from tests.utils import remote_config_build_payload as build_payload


def _record(config_id, lib_config, service_target=None):
    content = None
    if lib_config is not None:
        content = {"service_target": service_target or {"service": "*", "env": "*"}, "lib_config": lib_config}
    return Payload(
        {"id": config_id, "product_name": "APM_TRACING", "sha256_hash": None, "length": None, "tuf_version": 1},
        f"datadog/2/APM_TRACING/{config_id}/config",
        content,
    )


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


def test_unchanged_records_retained_across_updates():
    # Each call only carries the records that changed since the previous poll,
    # so an update to one record must not drop the settings of the others.
    callback = APMTracingCallback()

    callback._process_payloads(
        [
            _record("a", {"dynamic_instrumentation_enabled": True}),
            _record("b", {"tracing_sampling_rate": 0.5}),
        ]
    )
    chained_config = callback._process_payloads([_record("b", {"tracing_sampling_rate": 0.1})])

    assert dict(chained_config) == {"dynamic_instrumentation_enabled": True, "tracing_sampling_rate": 0.1}


def test_deleted_record_removed_from_merged_config():
    callback = APMTracingCallback()

    callback._process_payloads(
        [
            _record("a", {"dynamic_instrumentation_enabled": True}),
            _record("b", {"tracing_sampling_rate": 0.5}),
        ]
    )
    chained_config = callback._process_payloads([_record("a", None)])

    assert dict(chained_config) == {"tracing_sampling_rate": 0.5}

    assert dict(callback._process_payloads([_record("b", None)])) == {}


def test_retargeted_record_removed_from_merged_config(monkeypatch):
    monkeypatch.setattr(config, "service", "my-service")
    callback = APMTracingCallback()

    callback._process_payloads([_record("a", {"tracing_enabled": False}, {"service": "my-service", "env": "*"})])
    chained_config = callback._process_payloads(
        [_record("a", {"tracing_enabled": False}, {"service": "other-service", "env": "*"})]
    )

    assert dict(chained_config) == {}


def test_updated_record_takes_precedence_on_ties():
    callback = APMTracingCallback()

    callback._process_payloads(
        [_record("a", {"tracing_sampling_rate": 0.5}), _record("b", {"tracing_sampling_rate": 0.2})]
    )
    chained_config = callback._process_payloads([_record("a", {"tracing_sampling_rate": 0.9})])

    assert chained_config["tracing_sampling_rate"] == 0.9
