import typing as t

import pytest

from ddtrace import config
from ddtrace.internal.remoteconfig import ConfigMetadata
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.remoteconfig.products.apm_tracing import APMTracingCallback
from tests.utils import remote_config_build_payload as build_payload


def _record(
    config_id: str,
    lib_config: t.Optional[dict[str, t.Any]],
    service_target: t.Optional[dict[str, str]] = None,
    version: int = 1,
    path: t.Optional[str] = None,
) -> Payload:
    content: t.Optional[dict[str, t.Any]] = None
    if lib_config is not None:
        content = {"service_target": service_target or {"service": "*", "env": "*"}, "lib_config": lib_config}
    return Payload(
        ConfigMetadata(id=config_id, product_name="APM_TRACING", sha256_hash=None, length=None, tuf_version=version),
        path or f"datadog/2/APM_TRACING/{config_id}/config",
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

    # The settings of a deleted record are dispatched as None so handlers can
    # revert them, but only on the call that deletes it.
    assert dict(chained_config) == {"tracing_sampling_rate": 0.5, "dynamic_instrumentation_enabled": None}

    assert dict(callback._process_payloads([_record("b", None)])) == {"tracing_sampling_rate": None}
    assert dict(callback._process_payloads([])) == {}


def test_retargeted_record_removed_from_merged_config(monkeypatch):
    monkeypatch.setattr(config, "service", "my-service")
    callback = APMTracingCallback()

    callback._process_payloads([_record("a", {"tracing_enabled": False}, {"service": "my-service", "env": "*"})])
    chained_config = callback._process_payloads(
        [_record("a", {"tracing_enabled": False}, {"service": "other-service", "env": "*"}, version=2)]
    )

    # A record retargeted away from this process is removed for it.
    assert dict(chained_config) == {"tracing_enabled": None}


def test_updated_record_takes_precedence_on_ties():
    callback = APMTracingCallback()

    callback._process_payloads(
        [_record("a", {"tracing_sampling_rate": 0.5}), _record("b", {"tracing_sampling_rate": 0.2})]
    )
    chained_config = callback._process_payloads([_record("a", {"tracing_sampling_rate": 0.9}, version=2)])

    assert chained_config["tracing_sampling_rate"] == 0.9


def test_unchanged_record_keeps_precedence_on_ties():
    # A full snapshot, as delivered to a forked child or a new client, carries
    # the unchanged records again in any order. They must keep their place.
    callback = APMTracingCallback()

    callback._process_payloads(
        [_record("a", {"tracing_sampling_rate": 0.5}), _record("b", {"tracing_sampling_rate": 0.2})]
    )
    callback._process_payloads([_record("a", {"tracing_sampling_rate": 0.9}, version=2)])
    chained_config = callback._process_payloads(
        [_record("a", {"tracing_sampling_rate": 0.9}, version=2), _record("b", {"tracing_sampling_rate": 0.2})]
    )

    assert chained_config["tracing_sampling_rate"] == 0.9


def test_targeting_reevaluated_when_service_changes(monkeypatch):
    # Records are not delivered again when the service changes, so one that
    # did not target the previous service must apply once it targets this one.
    monkeypatch.setattr(config, "service", "inferred")
    callback = APMTracingCallback()

    chained_config = callback._process_payloads(
        [_record("a", {"tracing_sampling_rate": 0.3}, {"service": "checkout", "env": "*"})]
    )
    assert dict(chained_config) == {}

    monkeypatch.setattr(config, "service", "checkout")
    chained_config = callback._process_payloads([_record("b", {"tracing_enabled": True})])
    assert dict(chained_config) == {"tracing_sampling_rate": 0.3, "tracing_enabled": True}


def test_records_with_the_same_id_are_tracked_by_path():
    callback = APMTracingCallback()

    callback._process_payloads(
        [
            _record("a", {"tracing_sampling_rate": 0.5}),
            _record("a", {"tracing_enabled": True}, path="employee/APM_TRACING/a/config"),
        ]
    )
    chained_config = callback._process_payloads([_record("a", None, path="employee/APM_TRACING/a/config")])

    assert dict(chained_config) == {"tracing_sampling_rate": 0.5, "tracing_enabled": None}


def test_key_dropped_by_update_is_not_cleared():
    # An update that stops setting a key does not remove an override: only a
    # deleted or retargeted record does.
    callback = APMTracingCallback()

    callback._process_payloads([_record("a", {"exception_replay_enabled": True, "tracing_enabled": True})])
    chained_config = callback._process_payloads([_record("a", {"tracing_enabled": True}, version=2)])

    assert dict(chained_config) == {"tracing_enabled": True}


def test_deleted_key_still_set_by_another_record_is_not_cleared():
    callback = APMTracingCallback()

    callback._process_payloads(
        [_record("a", {"exception_replay_enabled": True}), _record("b", {"exception_replay_enabled": False})]
    )
    chained_config = callback._process_payloads([_record("b", None)])

    assert dict(chained_config) == {"exception_replay_enabled": True}


def test_deleted_record_not_targeting_this_process_is_not_cleared(monkeypatch):
    monkeypatch.setattr(config, "service", "my-service")
    callback = APMTracingCallback()

    callback._process_payloads([_record("a", {"exception_replay_enabled": True}, {"service": "other", "env": "*"})])
    chained_config = callback._process_payloads([_record("a", None)])

    assert dict(chained_config) == {}


@pytest.mark.parametrize(
    "content",
    [
        ["not", "a", "mapping"],
        {"service_target": "not-a-mapping", "lib_config": {"tracing_sampling_rate": 0.1}},
        {"service_target": {"service": "*", "env": "*"}, "lib_config": ["not", "a", "mapping"]},
    ],
)
def test_malformed_update_keeps_previous_record(content):
    callback = APMTracingCallback()

    callback._process_payloads(
        [_record("a", {"tracing_sampling_rate": 0.5}), _record("b", {"dynamic_instrumentation_enabled": True})]
    )
    malformed = _record("a", {})
    malformed = Payload(malformed.metadata, malformed.path, content)
    chained_config = callback._process_payloads([malformed])

    assert dict(chained_config) == {"tracing_sampling_rate": 0.5, "dynamic_instrumentation_enabled": True}
    # A later, unrelated update still merges the retained record.
    chained_config = callback._process_payloads([_record("b", {"dynamic_instrumentation_enabled": False})])
    assert dict(chained_config) == {"tracing_sampling_rate": 0.5, "dynamic_instrumentation_enabled": False}
