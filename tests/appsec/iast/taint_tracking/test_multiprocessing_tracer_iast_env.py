# -*- coding: utf-8 -*-
from multiprocessing import Process
from multiprocessing import Queue
import os
from time import sleep

import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import is_tainted
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from tests.appsec.iast.iast_utils import _end_iast_context_and_oce


def _child_check(q: Queue):
    """Subprocess entrypoint: report tracer and IAST env status back to parent via Queue."""
    try:
        from ddtrace.settings.asm import config as asm_config
        from ddtrace.trace import tracer

        text = "text_to_taint"
        text2 = b"text_to_taint" * 100
        tainted_text = taint_pyobject(
            text, source_name="request_body", source_value=text, source_origin=OriginType.PARAMETER
        )
        tainted_text2 = taint_pyobject(
            text2, source_name="request_body", source_value=text2, source_origin=OriginType.PARAMETER
        )
        # Fork once before reporting results to the parent Queue.
        # This exercises IAST and tracer state post-fork in a grandchild process.
        import os as _os

        pid = _os.fork()
        if pid == 0:
            # In forked grandchild: perform a quick no-op check to ensure no crash.
            assert bool(tracer.enabled)
            assert is_tainted(tainted_text)
            assert is_tainted(tainted_text2)
            _end_iast_context_and_oce()
            assert not is_tainted(tainted_text)
            # Exit immediately without touching the parent's Queue
            _os._exit(0)
        else:
            # Parent of fork: wait for grandchild to finish to ensure stability
            _os.waitpid(pid, 0)
        sleep(0.5)
        q.put(
            {
                "pid": os.getpid(),
                "tracer_enabled": bool(tracer.enabled),
                "iast_env": os.environ.get("DD_IAST_ENABLED"),
                "text_is_tainted": is_tainted(tainted_text),
                "text_is_tainted2": is_tainted(tainted_text2),
                "iast_enabled_flag": bool(asm_config._iast_enabled),
            }
        )
        _end_iast_context_and_oce()
        assert not is_tainted(tainted_text)
        assert not is_tainted(tainted_text2)
        taint_pyobject(
            tainted_text2, source_name="request_body", source_value=tainted_text2, source_origin=OriginType.PARAMETER
        )
    except Exception as e:
        q.put({"error": repr(e)})


@pytest.mark.skipif(os.name == "nt", reason="multiprocessing fork semantics differ on Windows")
def test_subprocess_has_tracer_running_and_iast_env(monkeypatch):
    """Verify a multiprocessing child sees tracer enabled and DD_IAST_ENABLED env propagated."""
    # Ensure tracer and IAST are enabled for the test
    monkeypatch.setenv("DD_IAST_ENABLED", "true")

    q: Queue = Queue()
    p = Process(target=_child_check, args=(q,), daemon=True)
    p.start()
    p.join(timeout=10)

    assert not p.is_alive(), "child process did not exit in time"
    assert p.exitcode is not None, "child did not set an exit code"

    result = q.get(timeout=5)
    assert "error" not in result, f"child error: {result.get('error')}"

    # Tracer should be enabled in child when DD_TRACE_ENABLED=true
    assert result["tracer_enabled"] is True

    # DD_IAST_ENABLED env should be visible and true in child
    assert result["iast_env"] in ("true", "True", "1")
    assert result["text_is_tainted"] is True
    assert result["text_is_tainted2"] is True

    # IAST enablement flag should reflect env
    assert result["iast_enabled_flag"] is True
