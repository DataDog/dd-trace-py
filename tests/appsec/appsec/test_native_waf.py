"""Native WAF contracts, conversion boundaries, configuration and lifetimes."""

import gc
import json
import os
import subprocess
import sys

import pytest

from ddtrace.appsec._waf import DDWaf
from ddtrace.internal import forksafe
from ddtrace.internal.native import _native


native = getattr(_native, "ddwaf", None)


RULES = {
    "version": "2.2",
    "rules": [
        {
            "id": "test-match",
            "name": "Test match",
            "tags": {"type": "security_scanner", "category": "attack_attempt"},
            "conditions": [
                {
                    "operator": "match_regex",
                    "parameters": {
                        "inputs": [{"address": "server.request.body"}],
                        "regex": "attack",
                    },
                }
            ],
            "on_match": ["block"],
        }
    ],
    "actions": [{"id": "block", "type": "block_request", "parameters": {"status_code": 403, "type": "auto"}}],
}
RULES_BYTES = json.dumps(RULES).encode()


@pytest.fixture
def waf():
    return DDWaf(RULES_BYTES, b"", b"")


def test_native_builder_subclass():
    assert isinstance(DDWaf(RULES_BYTES, b"", b""), native.Builder)


def test_required_data_and_diagnostics(waf):
    assert waf.initialized
    assert waf.required_data == ["server.request.body"]
    assert waf.info.accepted_rules == 1
    assert waf.info.rejected_rules == 0


@pytest.mark.parametrize("value", ["safe", "attack", {"text": "attack"}, ["safe", "attack"], "é🙂attack\ud800"])
def test_evaluation(value):
    waf = DDWaf(RULES_BYTES, b"", b"")
    result = waf.run(waf._at_request_start(), {"server.request.body": value}, timeout_ms=100)
    matched = value != "safe"
    assert result.matched == matched
    assert bool(result.events) == matched
    assert bool(result.actions) == matched
    if matched:
        assert result.actions["block_request"]["status_code"] == 403
    assert not result.timeout


def test_subcontext_does_not_change_parent(waf):
    parent = waf._at_request_start()
    assert waf.run(parent, {"server.request.body": "safe"}).matched is False
    child = waf.new_subcontext(parent)
    assert waf.run(child, {"server.request.body": "attack"}).matched is True
    assert waf.run(parent, {}).matched is False
    del parent
    gc.collect()
    assert waf.run(child, {}).matched is False  # cached inputs produce no second match


def test_rejected_update_retains_default(waf):
    required = waf.required_data
    assert not waf.update_rules([], [("ASM_DD", "ASM_DD/rejected", {"version": "2.2", "rules": "invalid"})])
    assert waf.initialized and waf.required_data == required
    assert waf.config_paths_count("ASM_DD/default") == 1
    assert waf.run(waf._at_request_start(), {"server.request.body": "attack"}).matched is True


def test_remove_remote_rules_restores_default(waf):
    replacement = json.loads(RULES_BYTES)
    replacement["rules"][0]["conditions"][0]["parameters"]["regex"] = "replacement"
    original_context = waf._at_request_start()
    assert waf.update_rules([], [("ASM_DD", "ASM_DD/replacement", replacement)])
    assert waf.config_paths_count("ASM_DD/replacement") == 1
    assert waf.config_paths_count("ASM_DD/default") == 0
    assert waf.run(original_context, {"server.request.body": "attack"}).matched is True
    assert waf.run(waf._at_request_start(), {"server.request.body": "attack"}).matched is False
    assert waf.update_rules([("ASM_DD", "ASM_DD/replacement")], [])
    assert waf.config_paths_count("ASM_DD/default") == 1
    assert waf.run(waf._at_request_start(), {"server.request.body": "attack"}).matched is True


def test_partial_rules_keep_diagnostics(waf):
    mixed = json.loads(RULES_BYTES)
    mixed["rules"].append({"id": "broken"})
    waf.update_rules([], [("ASM_DD", "ASM_DD/mixed", mixed)])
    assert waf.info.accepted_rules == 1 and waf.info.rejected_rules == 1
    assert any("broken" in rules for rules in waf.info.errors.values())


def test_stats_are_frozen_and_cached():
    value = native.encode({"key": "é🙂" * 100}, max_string_length=3)
    assert value.stats is value.stats
    assert gc.is_tracked(value)
    assert any(reference is value.stats for reference in gc.get_referents(value))
    assert value.stats.string_length == 600
    assert value.stats.container_size is None
    assert value.stats.nodes == 2
    with pytest.raises(AttributeError):
        value.stats.nodes = 0
    with pytest.raises(AttributeError):
        value.stats.arbitrary = 0


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires os.fork")
def test_fork_rejects_inherited_state_and_allows_fresh_builder():
    script = """
import gc
import json
import os
import sys
# Load the real extension without activating unrelated tracer products/fork hooks.
import importlib.util
import types
for name in ("ddtrace", "ddtrace.internal"):
    module = types.ModuleType(name)
    module.__path__ = []
    sys.modules[name] = module
hooks = types.ModuleType("ddtrace.internal.forksafe")
hooks.register = lambda callback: None
sys.modules[hooks.__name__] = hooks
spec = importlib.util.spec_from_file_location("_native", sys.argv[2])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
native = module.ddwaf
rules = json.loads(sys.argv[1])
builder = native.Builder()
assert builder.add_config("rules", rules)[0]
engine = builder.build()
context = engine.context()
pid = os.fork()
if pid == 0:
    for operation in (builder.build, engine.context, lambda: context.run({})):
        try:
            operation()
        except RuntimeError as error:
            assert "after fork" in str(error)
        else:
            os._exit(1)
    del context, engine, builder
    gc.collect()
    fresh = native.Builder()
    assert fresh.add_config("rules", rules)[0]
    assert fresh.build().context().run({"server.request.body": "attack"}).matched
    os._exit(0)
_, status = os.waitpid(pid, 0)
assert os.waitstatus_to_exitcode(status) == 0
"""
    subprocess.run([sys.executable, "-c", script, RULES_BYTES.decode(), _native.__file__], check=True, timeout=20)


@pytest.mark.parametrize("value", ["ascii", "é", "東京", "🙂", "a\x00b", "\ud800a\udfff", "é🙂" * 100, b"a\x00\xffb"])
@pytest.mark.parametrize("limit", [0, 1, 2, 3, 14, 15, 4096])
def test_raw_string_bytes_parity(value, limit):
    expected = (value if isinstance(value, bytes) else value.encode("utf-8", errors="ignore"))[:limit]
    assert native.encode(value, max_string_length=limit).bytes() == expected


def test_fork_clone_retains_remote_configs_and_obfuscation(waf, monkeypatch):
    replacement = json.loads(RULES_BYTES)
    replacement["rules"][0]["conditions"][0]["parameters"]["regex"] = "replacement"
    assert waf.update_rules([], [("ASM_DD", "ASM_DD/replacement", replacement)])
    # Mutating caller-owned input must not change the published replay snapshot.
    replacement["rules"].clear()
    monkeypatch.setattr(forksafe, "_fork_generation", forksafe.get_generation() + 1)
    assert waf.needs_rebuild
    child = waf.fork_clone()
    assert not child.needs_rebuild
    assert child.config_paths_count("ASM_DD/replacement") == 1
    assert child.run(child._at_request_start(), {"server.request.body": "replacement"}).matched is True
    assert child.run(child._at_request_start(), {"server.request.body": "attack"}).matched is False


def test_cyclic_inputs_are_bounded():
    value = []
    value.append(value)
    encoded = native.encode(value)
    assert encoded.stats.container_depth == 20


def test_strict_inputs_reject_user_callbacks():
    with pytest.raises(TypeError):
        native.encode(object())


def test_result_cache_cycles_are_collectable():
    engine = native.Engine.from_json(RULES_BYTES)
    result = engine.context().run({"server.request.body": "attack"})
    events = result.events
    events.append(result)
    identity = id(result)
    assert gc.is_tracked(result)
    del result, events
    gc.collect()
    assert all(id(value) != identity for value in gc.get_objects())


def test_registered_module_is_importable():
    import importlib

    assert importlib.import_module(native.__name__) is native
    assert native.Builder.__module__ == native.__name__


def test_native_error_is_adapted(waf):
    from unittest.mock import patch

    from ddtrace.appsec._waf import DDWafContext

    with patch.object(DDWafContext, "run", side_effect=native.EvaluationError(-3, "internal error")):
        result = waf.run(waf._at_request_start(), {})
    assert result.error_code == -3
    assert not result.events and not result.actions


def test_mutating_input_raises_python_error():
    data = {}

    class Mutator:
        def __str__(self):
            data["new"] = "value"
            return "value"

    data["original"] = Mutator()
    with pytest.raises(RuntimeError, match="dictionary changed size"):
        native.encode(data, compatibility=True)


@pytest.mark.parametrize("rules", [b"not JSON", {"value": float("nan")}, {"rules": object()}])
def test_unserializable_update_preserves_detection(waf, rules):
    assert not waf.update_rules([], [("ASM_DD", "ASM_DD/invalid", rules)])
    assert waf.config_paths_count("ASM_DD/default") == 1
    assert waf.run(waf._at_request_start(), {"server.request.body": "attack"}).matched is True


def test_result_properties_groups_and_gc():
    result = native.Result(
        matched=True,
        duration_ns=100,
        total_duration_ns=200,
        attributes={
            "tag": "value",
            "flag": True,
            "count": 2,
            "ratio": 0.5,
            "_dd.appsec.s.req.body": {"type": "object"},
        },
    )
    assert result.prepare() is result
    assert result.meta_tags == {"tag": "value"}
    assert result.metrics == {"flag": 1, "count": 2, "ratio": 0.5}
    assert type(result.metrics["flag"]) is int
    assert result.api_security == {"_dd.appsec.s.req.body": {"type": "object"}}
    assert result.metrics is result.metrics
    assert result.total_duration_ns == 200
    for name, value in [("matched", False), ("error_code", -3), ("total_duration_ns", 0)]:
        with pytest.raises(AttributeError):
            setattr(result, name, value)
    identity = id(result)
    result.metrics["cycle"] = result
    del result
    gc.collect()
    assert all(id(value) != identity for value in gc.get_objects() if isinstance(value, native.Result))


def test_ruleset_info_is_native_read_only_and_collectable():
    info = native.RulesetInfo("version", 1, 2, {"invalid": ["rule"]})
    assert info.errors == {"invalid": ["rule"]}
    with pytest.raises(AttributeError):
        info.accepted_rules = 0
    identity = id(info)
    info.errors["cycle"] = info
    del info
    gc.collect()
    assert all(id(value) != identity for value in gc.get_objects() if isinstance(value, native.RulesetInfo))


def test_result_preparation_freezes_complete_runtime(waf):
    result = waf.run(waf._at_request_start(), {"server.request.body": "attack"})
    assert isinstance(result, native.Result)
    assert result.error_code is None and result.matched
    elapsed = result.total_duration_ns
    assert elapsed >= result.duration_ns > 0
    assert result.prepare() is result
    assert result.total_duration_ns == elapsed
