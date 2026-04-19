"""Profiler + ASGI integration smoke test.

Exercises the continuous profiler while a FastAPI / Starlette app serves a
handful of requests. This catches combined regressions that neither the
synthetic `profile` suite nor the framework-only `fastapi`/`starlette` suites
catch on their own:

- Stack sampling of asyncio handlers running on a worker thread's event loop
  (starlette's TestClient drives the ASGI app from a background thread).
- asyncio task-name tracking through ASGI middleware chains.
- The 3.15 frame-layout port under real middleware stacks, not just synthetic
  coroutines.

The test is intentionally light on assertions (we only check that *some*
handler samples with task-name labels were captured) so that framework
version drift and timing flake do not make it noisy. If the profiler
completely fails to sample the ASGI event loop on a new Python release this
will still fail loudly.
"""

import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_profiling_asgi",
        DD_PROFILING_ENABLED="true",
    ),
    err=None,
)
def test_profiler_captures_fastapi_handler_frames():
    import asyncio
    import os

    fastapi = pytest.importorskip("fastapi")
    pytest.importorskip("httpx")

    from fastapi.testclient import TestClient

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    assert stack.is_available, stack.failure_msg

    app = fastapi.FastAPI()

    @app.get("/slow")
    async def slow_handler() -> dict:
        async def inner() -> None:
            await asyncio.sleep(0.2)

        task = asyncio.create_task(inner(), name="asgi-inner-task")
        await task
        return {"ok": True}

    p = profiler.Profiler()
    p.start()
    try:
        with TestClient(app) as client:
            for _ in range(5):
                resp = client.get("/slow")
                assert resp.status_code == 200
    finally:
        p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)

    # We should have at least one sample tagged with an asyncio task name;
    # this is the strongest single signal that profiler + ASGI event loop
    # integration works end-to-end.
    task_samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(task_samples) > 0, (
        "No asyncio task-name labels found in the profile — either the profiler "
        "did not sample the ASGI event loop thread or asyncio task tracking is broken."
    )

    handler_samples = [
        s
        for s in task_samples
        if any(
            pprof_utils.get_location_from_id(profile, loc_id).function_name in ("slow_handler", "inner")
            for loc_id in s.location_id
        )
    ]
    assert len(handler_samples) > 0, (
        "Profiler captured asyncio tasks but never saw our handler frames — "
        "stack unwinding through ASGI middleware may be broken."
    )
