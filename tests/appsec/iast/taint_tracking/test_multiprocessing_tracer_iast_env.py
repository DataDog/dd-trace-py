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
from tests.appsec.iast.iast_utils import _start_iast_context_and_oce


def _child_check(q: Queue):
    """
    Subprocess entrypoint: verify IAST is disabled in forked child processes.

    Reports tracer and IAST status back to parent via Queue.
    """
    try:
        from ddtrace.internal.settings.asm import config as asm_config
        from ddtrace.trace import tracer

        # Start IAST context in child process
        # Note: This will be a no-op since IAST is disabled after fork
        _start_iast_context_and_oce()

        text = "text_to_taint"
        text2 = b"text_to_taint" * 100
        # These taint operations will be no-ops since IAST is disabled after fork
        tainted_text = taint_pyobject(
            text, source_name="request_body", source_value=text, source_origin=OriginType.PARAMETER
        )
        tainted_text2 = taint_pyobject(
            text2, source_name="request_body", source_value=text2, source_origin=OriginType.PARAMETER
        )
        # Fork again to verify nested fork behavior
        import os as _os

        pid = _os.fork()
        if pid == 0:
            # In forked grandchild: IAST is disabled by fork handler
            assert bool(tracer.enabled)

            # After fork, IAST is disabled, so objects are NOT tainted
            assert not is_tainted(tainted_text), "IAST disabled after fork, so not tainted"
            assert not is_tainted(tainted_text2), "IAST disabled after fork, so not tainted"

            # Attempting to start IAST in grandchild should be a no-op
            _start_iast_context_and_oce()
            grandchild_text = taint_pyobject(
                "grandchild_data", source_name="test", source_value="test", source_origin=OriginType.PARAMETER
            )
            # IAST is disabled, so taint_pyobject is a no-op
            assert not is_tainted(grandchild_text)

            _end_iast_context_and_oce()
            assert not is_tainted(grandchild_text)
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


@pytest.mark.skip(reason="multiprocessing fork doesn't work correctly in ddtrace-py 4.0")
def test_subprocess_has_tracer_running_and_iast_env(monkeypatch):
    """
    Verify IAST is disabled in late fork multiprocessing scenarios.

    This test simulates multiprocessing.Process forking after IAST has active state.
    The fork handler should detect the active context and disable IAST in the child
    to prevent segmentation faults.

    This test verifies:
    - Tracer remains enabled in child
    - DD_IAST_ENABLED env variable is propagated
    - IAST is disabled (_iast_enabled = False) due to active context at fork time
    - taint_pyobject operations are no-ops (return non-tainted objects)

    Note: Web framework workers (early forks) that fork before IAST state exists
    will keep IAST enabled - this is tested separately in integration tests.
    """
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

    # DD_IAST_ENABLED env should be visible and true in child (env is inherited)
    assert result["iast_env"] in ("true", "True", "1")

    # But IAST should NOT actually taint objects (disabled for safety)
    assert result["text_is_tainted"] is False
    assert result["text_is_tainted2"] is False

    # IAST enablement flag should be False (disabled by fork handler)
    assert result["iast_enabled_flag"] is False
