"""
End-to-end tests for the EVP `flagevaluation` path through the real provider eval path.

These cover the lifecycle/exit paths that unit tests of the hook/writer in isolation
cannot prove:

- The EVP hook actually fires on the provider's REAL OpenFeature evaluation entrypoint
  (driven through the OpenFeature client, not by calling the hook directly).
- ALL evaluation exit paths are covered — success, native engine error, runtime default
  (flag-not-found / no-config), and disabled.
- The OTel `feature_flag.evaluations` path is preserved alongside the EVP path (non-regression).
"""

from unittest import mock

from openfeature import api
from openfeature.evaluation_context import EvaluationContext
import pytest

from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._native import process_ffe_configuration
from ddtrace.openfeature import DataDogProvider
from tests.openfeature.config_helpers import create_boolean_flag
from tests.openfeature.config_helpers import create_config
from tests.openfeature.config_helpers import create_string_flag
from tests.utils import override_global_config


@pytest.fixture(autouse=True)
def clear_config():
    _set_ffe_config(None)
    yield
    _set_ffe_config(None)


@pytest.fixture
def provider_and_client():
    """Set up a DataDogProvider with the EVP path enabled, returning (provider, client).

    The writer's background thread is NOT started (we drive aggregation synchronously via
    periodic()), so the eval path enqueues and we drain deterministically in the test.
    """
    with override_global_config({"experimental_flagging_provider_enabled": True}):
        provider = DataDogProvider()
    # Sanity: the EVP writer/hook are wired (killswitch default on).
    assert provider._flagevaluation_writer is not None
    assert provider._flagevaluation_hook is not None

    api.set_provider(provider)
    client = api.get_client()
    try:
        yield provider, client
    finally:
        api.shutdown()


def _drain(provider):
    """Aggregate everything the eval path enqueued, returning the emitted rows."""
    writer = provider._flagevaluation_writer
    with mock.patch.object(writer, "_send_payload") as mock_send:
        writer.periodic()
    if not mock_send.called:
        return []
    import json

    decoded = json.loads(mock_send.call_args[0][0])
    return decoded.get("flagEvaluations", [])


class TestEVPHookFiresOnRealEvalPath:
    """The EVP hook fires on the provider's real evaluation entrypoint."""

    def test_evp_hook_registered_in_provider_hooks(self, provider_and_client):
        provider, _ = provider_and_client
        from ddtrace.internal.openfeature._flagevaluation_hook import FlagEvaluationHook

        hooks = provider.get_provider_hooks()
        assert any(isinstance(h, FlagEvaluationHook) for h in hooks)

    def test_success_eval_enqueues_and_emits_row(self, provider_and_client):
        provider, client = provider_and_client
        config = create_config(create_boolean_flag("evp-success", enabled=True, default_value=True))
        process_ffe_configuration(config)

        # Real client eval — the OpenFeature SDK runs the registered finally_after hook.
        assert client.get_boolean_value("evp-success", False) is True

        rows = _drain(provider)
        keys = {r["flag"]["key"] for r in rows}
        assert "evp-success" in keys
        row = next(r for r in rows if r["flag"]["key"] == "evp-success")
        assert row["evaluation_count"] >= 1
        # Successful eval -> a real variant object, no runtime_default.
        assert "variant" in row and set(row["variant"].keys()) == {"key"}
        assert row.get("runtime_default_used", False) is False

    def test_variant_is_resolution_variant_not_value(self, provider_and_client):
        """End-to-end: emitted variant == resolution variant key, distinct from value."""
        provider, client = provider_and_client
        # String flag: value "blue", variant key "blue" (here equal); use details to confirm
        # the EVP row variant matches details.variant exactly.
        config = create_config(create_string_flag("evp-variant", "blue", enabled=True))
        process_ffe_configuration(config)

        details = client.get_string_details("evp-variant", "red")
        rows = _drain(provider)
        row = next(r for r in rows if r["flag"]["key"] == "evp-variant")
        assert row["variant"]["key"] == details.variant


class TestEVPExitPathsCovered:
    """success / engine-error / runtime-default / disabled exit paths are all captured."""

    def test_flag_not_found_runtime_default_path(self, provider_and_client):
        provider, client = provider_and_client
        # A config is loaded but the requested flag isn't in it -> native FlagNotFound -> ERROR.
        config = create_config(create_boolean_flag("present-flag", enabled=True))
        process_ffe_configuration(config)

        assert client.get_boolean_value("absent-flag", False) is False

        rows = _drain(provider)
        row = next((r for r in rows if r["flag"]["key"] == "absent-flag"), None)
        assert row is not None, "flag-not-found eval must still emit a flagevaluation row"
        # No variant -> runtime_default_used True.
        assert row.get("runtime_default_used") is True
        assert "variant" not in row

    def test_no_config_provider_not_ready_path(self, provider_and_client):
        provider, client = provider_and_client
        _set_ffe_config(None)  # no RC config at all -> PROVIDER_NOT_READY error path

        assert client.get_boolean_value("no-config-flag", True) is True

        rows = _drain(provider)
        row = next((r for r in rows if r["flag"]["key"] == "no-config-flag"), None)
        assert row is not None, "no-config eval must still emit a flagevaluation row"
        assert row.get("runtime_default_used") is True

    def test_type_mismatch_error_path(self, provider_and_client):
        provider, client = provider_and_client
        config = create_config(create_string_flag("str-flag", "hello", enabled=True))
        process_ffe_configuration(config)

        # Evaluate a string flag as boolean -> type mismatch ERROR.
        assert client.get_boolean_value("str-flag", False) is False

        rows = _drain(provider)
        row = next((r for r in rows if r["flag"]["key"] == "str-flag"), None)
        assert row is not None, "type-mismatch eval must still emit a flagevaluation row"
        assert row.get("runtime_default_used") is True

    def test_disabled_flag_path(self, provider_and_client):
        provider, client = provider_and_client
        config = create_config(create_boolean_flag("disabled-flag", enabled=False, default_value=False))
        process_ffe_configuration(config)

        assert client.get_boolean_value("disabled-flag", False) is False

        rows = _drain(provider)
        # Disabled flag still produces a flagevaluation row.
        row = next((r for r in rows if r["flag"]["key"] == "disabled-flag"), None)
        assert row is not None, "disabled-flag eval must still emit a flagevaluation row"

    def test_targeting_key_and_context_captured_on_success(self, provider_and_client):
        provider, client = provider_and_client
        config = create_config(create_boolean_flag("ctx-flag", enabled=True, default_value=True))
        process_ffe_configuration(config)

        ctx = EvaluationContext(targeting_key="user-77", attributes={"tier": "gold"})
        client.get_boolean_value("ctx-flag", False, ctx)

        rows = _drain(provider)
        row = next(r for r in rows if r["flag"]["key"] == "ctx-flag")
        assert row.get("targeting_key") == "user-77"
        assert row["context"]["evaluation"]["tier"] == "gold"


class TestOTelNonRegressionAlongsideEVP:
    """The EVP path must NOT change which hooks the provider registers for OTel (non-regression)."""

    def test_both_otel_and_evp_hooks_registered(self, provider_and_client):
        provider, _ = provider_and_client
        from ddtrace.internal.openfeature._flageval_metrics import FlagEvalHook
        from ddtrace.internal.openfeature._flagevaluation_hook import FlagEvaluationHook

        hooks = provider.get_provider_hooks()
        assert any(isinstance(h, FlagEvalHook) for h in hooks), "OTel hook must remain registered"
        assert any(isinstance(h, FlagEvaluationHook) for h in hooks), "EVP hook must be registered"

    def test_eval_drives_otel_record_and_evp_enqueue_together(self, provider_and_client):
        """A single eval feeds BOTH the OTel metric record and the EVP enqueue."""
        provider, client = provider_and_client
        config = create_config(create_boolean_flag("dual-flag", enabled=True, default_value=True))
        process_ffe_configuration(config)

        # Spy the OTel metrics record and the EVP writer enqueue.
        with (
            mock.patch.object(provider._flag_eval_metrics, "record") as otel_record,
            mock.patch.object(provider._flagevaluation_writer, "enqueue") as evp_enqueue,
        ):
            client.get_boolean_value("dual-flag", False)

        assert otel_record.called, "OTel feature_flag.evaluations record must still fire"
        assert evp_enqueue.called, "EVP flagevaluation enqueue must fire"
