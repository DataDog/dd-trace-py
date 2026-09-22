import sys

import pytest


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess(
    env={
        "_DD_PROFILING_STACK_FAST_COPY": "1",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=lambda s: "falling back to syscall" not in s,
)
def test_sampler_reclaims_crashtracker_overwrite() -> None:
    """If crashtracker overwrites SIGSEGV after warmup, reclaim on top and keep fast copy.

    Do not pin the syscall fallback: crashtracker is ours and can sit under the
    stack catcher. Sampling faults longjmp; real crashes chain to crashtracker.
    """
    import time
    from typing import Any
    from typing import Callable
    from typing import Optional

    from ddtrace.internal.core import crashtracking
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.internal.settings.crashtracker import config as crashtracker_config

    assert crashtracking.is_available
    assert stack.is_available

    ddup.config(env="test", service="test", version="0.0.0")
    ddup.start()
    _stack._set_fast_copy_warmup_seconds(0.5)
    stack.set_adaptive_sampling(False)
    started: bool = stack.start()
    assert started

    try:
        saw_upgrade: bool = False
        upgrade_deadline: float = time.monotonic() + 10
        while time.monotonic() < upgrade_deadline:
            if _stack.fast_copy_memory_active() is True and stack.segv_handler_installed():
                saw_upgrade = True
                break
            time.sleep(0.05)
        assert saw_upgrade, "sampler never upgraded to safe_memcpy"

        crashtracker_config.debug_url = "http://127.0.0.1:9"
        crashtracker_config._stacktrace_resolver = "safe"
        init_fn: Callable[..., object] = getattr(crashtracking, "crashtracker_init")
        args: Any = crashtracking._get_args({"service": "reclaim_native"})
        assert args[0] is not None

        pause_result: Optional[bool] = stack.pause_sampling()
        assert pause_result is not None, "sampler pause timed out before overwrite"
        try:
            init_fn(*args)
            assert stack.segv_handler_installed() is False
        finally:
            if pause_result is True:
                stack.resume_sampling()

        reclaimed: bool = False
        reclaim_deadline: float = time.monotonic() + 10
        while time.monotonic() < reclaim_deadline:
            if stack.segv_handler_installed() and _stack.fast_copy_memory_active():
                reclaimed = True
                break
            time.sleep(0.05)
        assert reclaimed, "sampler did not reclaim crashtracker SIGSEGV / keep fast copy"
    finally:
        stack.stop()
