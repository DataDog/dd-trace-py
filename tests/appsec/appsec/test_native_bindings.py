"""Exercise the assembled native binary without activating tracer products.

DD_WAF_TEST_EXTENSION can select a binary built for another interpreter or target.
"""

import os
from pathlib import Path
import subprocess
import sys
import sysconfig

import pytest


ROOT = Path(__file__).resolve().parents[3]

LOADER = """
import gc
import importlib.util
import json
import os
import sys
import types
# _native's random-generator hook imports forksafe during registration. Isolate
# that unrelated product hook, keeping every WAF operation and allocation real.
for name in ("ddtrace", "ddtrace.internal"):
    module = types.ModuleType(name)
    module.__path__ = []
    sys.modules[name] = module
hooks = types.ModuleType("ddtrace.internal.forksafe")
hooks.register = lambda callback: None
sys.modules[hooks.__name__] = hooks
spec = importlib.util.spec_from_file_location("_native", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
waf = module.ddwaf
rules = json.dumps({"version": "2.2", "rules": [{
    "id": "test", "name": "test", "tags": {"type": "test", "category": "test"},
    "conditions": [{"operator": "match_regex", "parameters": {
        "inputs": [{"address": "body"}], "regex": "attack"}}]}]}).encode()
"""


def probe(code):
    artifact = os.environ.get("DD_WAF_TEST_EXTENSION") or str(
        ROOT / "ddtrace/internal/native" / ("_native" + sysconfig.get_config_var("EXT_SUFFIX"))
    )
    assert Path(artifact).is_file(), artifact
    subprocess.run([sys.executable, "-c", LOADER + code, artifact], check=True, timeout=30)


def test_unicode_buffers_limits_and_stats():
    probe("""
for value in ["ascii", "é", "東京", "🙂", "a\\x00b", "\\ud800a\\udfff", "é🙂" * 100, b"a\\x00\\xffb"]:
    expected = value if isinstance(value, bytes) else value.encode("utf8", "ignore")
    for limit in [0, 1, 2, 3, 14, 15, 4096]:
        result = waf.encode(value, max_string_length=limit)
        assert result.bytes() == expected[:limit]
        assert result.stats is result.stats
        assert result.stats.string_length == (len(expected) if len(expected) > limit else None)
        try:
            result.stats.nodes = 0
        except AttributeError:
            pass
        else:
            raise AssertionError("mutable Stats")
cyclic = []; cyclic.append(cyclic)
assert waf.encode(cyclic).stats.container_depth == 20
assert len(waf.encode(list(range(70000)), max_objects=70000).materialize()) == 65535
assert waf.encode((1 << 64) - 1).materialize() == -1
""")


def test_snapshots_subcontexts_threads_and_gc():
    probe("""
from concurrent.futures import ThreadPoolExecutor
builder = waf.Builder()
assert builder.add_config("rules", rules)[0]
engine = builder.build()
assert engine.required_data == ["body"]
context = engine.context()
subcontext = context.subcontext()
assert subcontext.run({"body": "attack"}).matched
assert not context.run({}).matched
builder.remove_config("rules")
assert builder.build() is None
assert context.run({"body": "attack"}).matched
shared = engine.context()
with ThreadPoolExecutor(max_workers=4) as executor:
    results = list(executor.map(lambda _: shared.run({"body": "attack"}).matched, range(32)))
assert sum(results) == 1
result = engine.context().run({"body": "attack"})
identity = id(result)
events = result.events
events.append(result)
assert gc.is_tracked(result)
del events, result
gc.collect()
assert all(id(value) != identity for value in gc.get_objects() if isinstance(value, waf.Result))
""")


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires os.fork")
def test_fork_ownership():
    probe("""
builder = waf.Builder()
assert builder.add_config("rules", rules)[0]
engine = builder.build()
context = engine.context()
pid = os.fork()
if pid == 0:
    try:
        for operation in [builder.build, engine.context, lambda: context.run({})]:
            try:
                operation()
            except RuntimeError as error:
                assert "after fork" in str(error)
            else:
                raise AssertionError("inherited state reused")
        del builder, engine, context
        gc.collect()
        fresh = waf.Builder()
        assert fresh.add_config("rules", rules)[0]
        assert fresh.build().context().run({"body": "attack"}).matched
    except BaseException:
        import traceback
        traceback.print_exc()
        os._exit(1)
    os._exit(0)
_, status = os.waitpid(pid, 0)
assert os.waitstatus_to_exitcode(status) == 0
""")
