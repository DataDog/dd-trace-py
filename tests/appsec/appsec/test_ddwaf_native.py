"""Rust WAF conversion and native lifetime regressions."""

from concurrent.futures import ThreadPoolExecutor
import gc
import os
import subprocess
import sys

import pytest

from ddtrace.appsec._ddwaf.waf import DDWaf
from ddtrace.internal.native._native import ddwaf as native
import tests.appsec.rules as rules


def test_conversion_errors_propagate():
    class Boom:
        def __str__(self):
            raise ZeroDivisionError("boom")

    with pytest.raises(ZeroDivisionError):
        native._encode({"a": ["x" * 100, {"b": Boom()}]})


def test_dict_mutation_during_conversion_raises():
    data = {}

    class Mutator:
        def __str__(self):
            data["new"] = "value"
            return "value"

    data["original"] = Mutator()
    data["other"] = 1
    with pytest.raises(RuntimeError, match="changed size"):
        native._encode(data)


@pytest.fixture
def waf():
    with open(rules.RULES_GOOD_PATH, "rb") as f:
        return DDWaf(f.read(), b"", b"")


SQLI = {"server.request.cookies": {"attack": "1' or '1' = '1'"}}


def test_subcontext_data_does_not_persist(waf):
    ctx = waf._at_request_start()
    sub = waf.new_subcontext(ctx)
    assert waf.run(sub, SQLI).data
    # The parent never saw the subcontext data, so it can still match it.
    assert waf.run(ctx, SQLI).data


def test_subcontext_outlives_parent(waf):
    ctx = waf._at_request_start()
    assert not waf.run(ctx, {"server.request.query": {"q": "hello"}}).data
    sub = waf.new_subcontext(ctx)
    del ctx
    gc.collect()
    assert waf.run(sub, SQLI).data


def test_context_survives_rules_update(waf):
    ctx = waf._at_request_start()
    replacement = {
        "version": "2.2",
        "rules": [
            {
                "id": "replacement",
                "name": "replacement",
                "tags": {"type": "test", "category": "test"},
                "conditions": [
                    {
                        "operator": "match_regex",
                        "parameters": {"inputs": [{"address": "server.request.query"}], "regex": "replacement"},
                    }
                ],
            }
        ],
    }
    assert waf.update_rules([], [("ASM_DD", "ASM_DD/replacement", replacement)])
    gc.collect()
    # The old context keeps its ruleset generation.
    assert waf.run(ctx, SQLI).data
    new_ctx = waf._at_request_start()
    assert not waf.run(new_ctx, SQLI).data
    assert waf.run(new_ctx, {"server.request.query": {"q": "replacement"}}).data
    assert waf.update_rules([("ASM_DD", "ASM_DD/replacement")], [])
    assert waf.run(waf._at_request_start(), SQLI).data


def test_concurrent_runs_on_shared_context(waf):
    ctx = waf._at_request_start()
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(lambda i: bool(waf.run(ctx, {"server.request.query": {"q": str(i)}}).data), range(64))
        )
    assert not any(results)
    with ThreadPoolExecutor(max_workers=8) as executor:
        matches = list(executor.map(lambda _: bool(waf.run(waf.new_subcontext(ctx), SQLI).data), range(64)))
    assert all(matches)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires os.fork")
def test_fork_ownership(tmp_path):
    script = """
import gc, json, os
from ddtrace.appsec._ddwaf.waf import DDWaf
rules = json.dumps({
    "version": "2.2",
    "rules": [{
        "id": "r1",
        "name": "r1",
        "tags": {"type": "t", "category": "c"},
        "conditions": [{"operator": "match_regex", "parameters": {"inputs": [{"address": "q"}], "regex": "attack"}}],
    }],
}).encode()
inst = DDWaf(rules, b"", b"")
ctx = inst._at_request_start()
pid = os.fork()
if pid == 0:
    try:
        for fn in (ctx.native.subcontext, lambda: ctx.run({}, 1000)):
            try:
                fn()
            except RuntimeError as e:
                assert "after fork" in str(e)
            else:
                raise AssertionError("inherited request context allowed after fork")
        del ctx
        gc.collect()
        # Prefork workers inherit the compiled DDWaf handle and builder state.
        child_ctx = inst._at_request_start()
        assert inst.run(child_ctx, {"q": "attack"}).data
        assert inst.update_rules([], [("ASM_DD", "ASM_DD/child", json.loads(rules))])
        assert inst.run(inst._at_request_start(), {"q": "attack"}).data
    except BaseException:
        import traceback
        traceback.print_exc()
        os._exit(1)
    os._exit(0)
_, status = os.waitpid(pid, 0)
assert os.waitstatus_to_exitcode(status) == 0
"""
    subprocess.run([sys.executable, "-c", script], check=True, timeout=60, cwd=tmp_path)
