"""Transport-to-evaluator tests using local HTTP endpoints and real native clients.

The endpoints model capability-based selection; these tests do not validate the
Remote Config service's targeting or a deployed Agent. Configuration parsing,
capability serialization, native evaluation, and provider event emission are real.
"""

import base64
import copy
import hashlib
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
import json
import threading
from unittest import mock

from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ErrorCode
import pytest

from ddtrace.internal.openfeature import _agentless_source
from ddtrace.internal.openfeature import _remoteconfiguration
from ddtrace.internal.openfeature._config import _get_ffe_config
from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._native import DEPENDENT_FLAG_VARIANT_KEY_V1
from ddtrace.internal.openfeature._native import get_evaluator_capabilities
from ddtrace.internal.openfeature._provider import _apply_agentless_configuration
from ddtrace.internal.remoteconfig.worker import RemoteConfigPoller
from ddtrace.openfeature import DataDogProvider
from tests.openfeature.config_helpers import create_boolean_flag
from tests.openfeature.config_helpers import create_config
from tests.utils import override_global_config


_CAPABILITY_HEADER = "X-DD-FEATURE-FLAGS-CAPABILITIES"
_CONTEXT = EvaluationContext(targeting_key="subject-1", attributes={"plan": "premium"})


def _prerequisite(key="parent", operator="ONE_OF", values=None):
    return {"flagEvaluation": {"key": key}, "operator": operator, "value": ["false"] if values is None else values}


def _attribute(value="premium"):
    return {"attribute": "plan", "operator": "ONE_OF", "value": [value]}


def _configuration():
    # A successful false value must be matched by its selected variant key; it
    # cannot be mistaken for an unsuccessful prerequisite evaluation.
    parent = create_boolean_flag("parent", default_value=False)
    child = create_boolean_flag("child")
    child["allocations"][0].update(key="targeted", rules=[{"conditions": [_prerequisite(), _attribute()]}])
    child["allocations"].append({"key": "fallback", "splits": [{"variationKey": "false", "shards": []}], "doLog": True})
    config = create_config(parent, child, create_boolean_flag("unrelated"))
    config["evaluatorParams"] = {"maxDependencyDepth": 10}
    return config


def _compatibility(config):
    compatible = copy.deepcopy(config)
    compatible["flags"] = {
        key: flag
        for key, flag in compatible["flags"].items()
        if not any(
            "flagEvaluation" in condition
            for allocation in flag.get("allocations", [])
            for rule in allocation.get("rules", [])
            for condition in rule.get("conditions", [])
        )
    }
    compatible.pop("evaluatorParams", None)
    return compatible


def _encoded(value):
    return base64.b64encode(json.dumps(value).encode()).decode()


class _ConfigurationEndpoint:
    def __init__(self):
        self.configuration = _configuration()
        self.rollout_enabled = True
        self.version = 1
        self.requests = []
        endpoint = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def _send(self, body, status=200, headers=None):
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                if self.path != "/ufc":
                    self._send({"endpoints": ["/v0.7/config"]})
                    return
                token = self.headers.get(_CAPABILITY_HEADER, "")
                supported = DEPENDENT_FLAG_VARIANT_KEY_V1 in token.split(",")
                endpoint.requests.append({"header": token})
                config, variant = endpoint.selected(supported)
                etag = f'"{endpoint.version}-{variant}"'
                if self.headers.get("If-None-Match") == etag:
                    self._send({}, status=304, headers={"ETag": etag})
                    return
                self._send(
                    {"data": {"id": "ufc", "type": "universal-flag-configuration", "attributes": config}},
                    headers={"ETag": etag},
                )

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                endpoint.requests.append(request)
                capabilities = request["client"]["capabilities"]
                raw = base64.b64decode(capabilities) if isinstance(capabilities, str) else bytes(capabilities)
                config, variant = endpoint.selected(bool(int.from_bytes(raw, "big") & (1 << 50)))
                path = f"datadog/2/FFE_FLAGS/ufc-{variant}/config"
                contents = json.dumps(config).encode()
                self._send(
                    {
                        "targets": _encoded(
                            {
                                "signed": {
                                    "_type": "targets",
                                    "spec_version": "1.0",
                                    "version": endpoint.version,
                                    "expires": "2099-01-01T00:00:00Z",
                                    "custom": {"opaque_backend_state": _encoded({})},
                                    "targets": {
                                        path: {
                                            "custom": {"v": endpoint.version},
                                            "length": len(contents),
                                            "hashes": {"sha256": hashlib.sha256(contents).hexdigest()},
                                        }
                                    },
                                },
                                "signatures": [],
                            }
                        ),
                        "target_files": [{"path": path, "raw": base64.b64encode(contents).decode()}],
                        "client_configs": [path],
                    }
                )

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def selected(self, supported):
        if supported and self.rollout_enabled:
            return self.configuration, "dependent"
        return _compatibility(self.configuration), "compatible"

    def publish(self, configuration=None, rollout_enabled=True):
        if configuration is not None:
            self.configuration = configuration
        self.rollout_enabled = rollout_enabled
        self.version += 1

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture(params=["agentless", "remote_config"])
def delivery(request, monkeypatch):
    _set_ffe_config(None)
    with _ConfigurationEndpoint() as endpoint:
        if request.param == "agentless":
            source = _agentless_source.AgentlessConfigurationSource(
                endpoint=endpoint.url + "/ufc", apply_configuration=_apply_agentless_configuration, api_key="test-key"
            )
            endpoint.poll = source.periodic
            endpoint.disable_capability = lambda: monkeypatch.setattr(
                _agentless_source, "get_evaluator_capabilities", lambda: ()
            )
        else:
            poller = RemoteConfigPoller()
            poller._client.agent_url = endpoint.url
            # Drive polls synchronously while keeping the production capability
            # registration, client serialization, callback, and parser intact.
            monkeypatch.setattr(poller, "enable", lambda: True)
            monkeypatch.setattr(_remoteconfiguration, "remoteconfig_poller", poller)
            monkeypatch.setattr(
                _remoteconfiguration, "_featureflag_rc_callback", _remoteconfiguration.FeatureFlagCallback()
            )
            _remoteconfiguration.enable_featureflags_rc()
            endpoint.poll = poller._client.request
            endpoint.disable_capability = lambda: poller.update_capabilities(
                [_remoteconfiguration.RemoteConfigCapabilities.FfeFlagDependentFlagVariantKeyV1], []
            )
        endpoint.transport = request.param
        yield endpoint
    _set_ffe_config(None)


@pytest.fixture
def provider(monkeypatch):
    writer = mock.Mock()
    monkeypatch.setattr("ddtrace.internal.openfeature._provider.get_exposure_writer", lambda: writer)
    with override_global_config({"experimental_flagging_provider_enabled": True}):
        instance = DataDogProvider()
        instance._flag_eval_evp_writer = mock.Mock()
        yield instance, writer


def _exposed(writer):
    return [call.args[0]["flag"]["key"] for call in writer.enqueue.call_args_list]


def _evaluated(provider):
    return [call.args[0].flag_key for call in provider._flag_eval_evp_writer.enqueue.call_args_list]


def test_capability_advertisement_delivers_evaluable_dependencies(delivery, provider):
    assert DEPENDENT_FLAG_VARIANT_KEY_V1 in get_evaluator_capabilities()
    instance, writer = provider
    delivery.poll()
    assert _get_ffe_config() is not None
    result = instance.resolve_boolean_details("child", False, _CONTEXT)
    assert result.value is True
    assert result.variant == "true"
    assert result.error_code is None
    assert sorted(_exposed(writer)) == ["child", "parent"]
    assert _evaluated(instance) == ["parent"]
    if delivery.transport == "agentless":
        assert delivery.requests[-1]["header"] == DEPENDENT_FLAG_VARIANT_KEY_V1
    else:
        raw = delivery.requests[-1]["client"]["capabilities"]
        raw = base64.b64decode(raw) if isinstance(raw, str) else bytes(raw)
        assert int.from_bytes(raw, "big") & ((1 << 46) | (1 << 50)) == (1 << 46) | (1 << 50)


@pytest.mark.parametrize("disable", ["rollout", "capability"])
def test_compatibility_replaces_dependency_configuration(delivery, provider, disable):
    instance, writer = provider
    delivery.poll()
    assert instance.resolve_boolean_details("child", False, _CONTEXT).value is True
    writer.reset_mock()
    if disable == "capability":
        delivery.disable_capability()
    delivery.publish(rollout_enabled=disable != "rollout")
    delivery.poll()
    result = instance.resolve_boolean_details("child", False, _CONTEXT)
    assert result.value is False
    assert result.error_code == ErrorCode.FLAG_NOT_FOUND
    assert _exposed(writer) == []
    assert instance.resolve_boolean_details("unrelated", False, _CONTEXT).value is True


def test_rollout_reenable_and_changed_prerequisite_do_not_reuse_old_evaluation(delivery, provider):
    instance, _ = provider
    delivery.publish(rollout_enabled=False)
    delivery.poll()
    assert instance.resolve_boolean_details("child", False, _CONTEXT).error_code == ErrorCode.FLAG_NOT_FOUND
    delivery.publish(rollout_enabled=True)
    delivery.poll()
    assert instance.resolve_boolean_details("child", False, _CONTEXT).value is True
    updated = _configuration()
    updated["flags"]["parent"]["allocations"][0]["splits"][0]["variationKey"] = "true"
    delivery.publish(updated)
    delivery.poll()
    assert instance.resolve_boolean_details("child", True, _CONTEXT).value is False


@pytest.mark.parametrize("prerequisite_first", [False, True])
def test_authored_order_controls_prerequisite_evaluation_and_exposure(delivery, provider, prerequisite_first):
    instance, writer = provider
    config = _configuration()
    conditions = [_prerequisite(), _attribute("different-plan")]
    if not prerequisite_first:
        conditions.reverse()
    config["flags"]["child"]["allocations"][0]["rules"] = [{"conditions": conditions}]
    delivery.publish(config)
    delivery.poll()
    assert instance.resolve_boolean_details("child", True, _CONTEXT).value is False
    assert _exposed(writer) == ["child"]
    assert _evaluated(instance) == (["parent"] if prerequisite_first else [])


def test_expanded_rules_memoize_prerequisite_and_expose_only_successful_path(delivery, provider):
    instance, writer = provider
    config = _configuration()
    config["flags"]["child"]["allocations"][0]["rules"] = [
        {"conditions": [_prerequisite(), _attribute("different-plan")]},
        {"conditions": [_prerequisite(), _attribute()]},
    ]
    delivery.publish(config)
    delivery.poll()
    for _ in range(2):
        assert instance.resolve_boolean_details("child", False, _CONTEXT).value is True
    # Memoization lasts for one root evaluation; exposure dedup spans calls.
    assert _evaluated(instance) == ["parent", "parent"]
    assert sorted(_exposed(writer)) == ["child", "parent"]


def test_memoized_exposures_replay_successful_path_order_not_completion_order(delivery, provider):
    instance, writer = provider
    config = _configuration()
    config["flags"]["sibling"] = create_boolean_flag("sibling", default_value=False)
    config["flags"]["child"]["allocations"][0]["rules"] = [
        {"conditions": [_prerequisite(), _attribute("different-plan")]},
        {"conditions": [_prerequisite("sibling"), _prerequisite(), _attribute()]},
    ]
    delivery.publish(config)
    delivery.poll()

    assert instance.resolve_boolean_details("child", False, _CONTEXT).value is True
    assert _evaluated(instance) == ["parent", "sibling"]
    assert _exposed(writer) == ["sibling", "parent", "child"]


@pytest.mark.parametrize(
    "operator,values,matched",
    [("ONE_OF", ["false"], True), ("NOT_ONE_OF", ["true"], True), ("NOT_ONE_OF", ["false"], False)],
)
def test_prerequisite_membership_matches_variant_keys(delivery, provider, operator, values, matched):
    instance, writer = provider
    config = _configuration()
    config["flags"]["child"]["allocations"][0]["rules"] = [
        {"conditions": [_prerequisite(operator=operator, values=values)]}
    ]
    delivery.publish(config)
    delivery.poll()
    result = instance.resolve_boolean_details("child", not matched, _CONTEXT)
    assert result.value is matched
    assert result.error_code is None
    assert sorted(_exposed(writer)) == (["child", "parent"] if matched else ["child"])
    assert _evaluated(instance) == ["parent"]


@pytest.mark.parametrize("parent_logging,child_logging", [(False, True), (True, False), (False, False)])
def test_dependency_and_root_exposures_respect_do_log(delivery, provider, parent_logging, child_logging):
    instance, writer = provider
    config = _configuration()
    config["flags"]["parent"]["allocations"][0]["doLog"] = parent_logging
    config["flags"]["child"]["allocations"][0]["doLog"] = child_logging
    delivery.publish(config)
    delivery.poll()
    assert instance.resolve_boolean_details("child", False, _CONTEXT).value is True
    assert sorted(_exposed(writer)) == sorted(
        key for key, enabled in [("parent", parent_logging), ("child", child_logging)] if enabled
    )
    assert _evaluated(instance) == ["parent"]


def test_transitive_prerequisite_on_rejected_parent_rule_has_no_exposure(delivery, provider):
    instance, writer = provider
    config = _configuration()
    config["flags"]["leaf"] = create_boolean_flag("leaf", default_value=False)
    parent = config["flags"]["parent"]
    rejected = copy.deepcopy(parent["allocations"][0])
    rejected.update(key="rejected-parent-rule", rules=[{"conditions": [_prerequisite("leaf"), _attribute("other")]}])
    parent["allocations"].insert(0, rejected)
    delivery.publish(config)
    delivery.poll()
    assert instance.resolve_boolean_details("child", False, _CONTEXT).value is True
    assert sorted(_evaluated(instance)) == ["leaf", "parent"]
    assert sorted(_exposed(writer)) == ["child", "parent"]


def test_later_dependency_error_suppresses_earlier_successful_prerequisite_exposure(delivery, provider):
    instance, writer = provider
    config = _configuration()
    config["flags"]["child"]["allocations"][0]["rules"] = [{"conditions": [_prerequisite(), _prerequisite("missing")]}]
    delivery.publish(config)
    delivery.poll()
    result = instance.resolve_boolean_details("child", False, _CONTEXT)
    assert result.value is False
    assert result.error_code is not None
    assert _evaluated(instance) == ["parent", "missing"]
    assert _exposed(writer) == []


@pytest.mark.parametrize("failure", ["missing", "disabled", "unassigned", "malformed", "cycle", "depth"])
def test_failed_prerequisite_returns_caller_default_without_exposures(delivery, provider, failure):
    instance, writer = provider
    config = _configuration()
    parent = config["flags"]["parent"]
    if failure == "missing":
        del config["flags"]["parent"]
    elif failure == "disabled":
        parent["enabled"] = False
    elif failure == "unassigned":
        parent["allocations"] = []
    elif failure == "malformed":
        parent["variationType"] = "UNKNOWN"
    else:
        parent["allocations"][0]["rules"] = [{"conditions": [_prerequisite("child" if failure == "cycle" else "leaf")]}]
        if failure == "depth":
            config["flags"]["leaf"] = create_boolean_flag("leaf", default_value=False)
            config["evaluatorParams"]["maxDependencyDepth"] = 1
    delivery.publish(config)
    delivery.poll()
    result = instance.resolve_boolean_details("child", False, _CONTEXT)
    assert result.value is False
    assert result.error_code is not None
    assert _exposed(writer) == []
    assert "parent" in _evaluated(instance)
    assert instance.resolve_boolean_details("unrelated", False, _CONTEXT).value is True
