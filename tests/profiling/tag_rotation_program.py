#!/usr/bin/env python
"""Manual-profiling driver that rotates a per-upload tag.

Runs three upload cycles, each tagged with a different ``phase`` value.
With the exporter now reused across cycles and ``user_tags`` shipped via
``optional_additional_tags``, every cycle should land at the agent carrying
its own ``phase:<value>`` tag.

Usage (standalone, against a real agent on a profiler host):

    DD_TRACE_AGENT_URL=http://localhost:8126 \
    DD_PROFILING_ENABLED=1 \
    python tests/profiling/tag_rotation_program.py

The script prints the cycle/phase it just uploaded so a wrapping test
(see ``test_per_upload_tags.py``) can correlate captured HTTP bodies.
"""
import os
import sys
import time

import ddtrace
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling import Profiler


PHASES = ("setup", "warmup", "production")


def busy_loop(seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        sum(range(10_000))


def main() -> int:
    tracer = ddtrace.tracer
    profiler = Profiler(service="tag-rotation-test", env="test", version="0.1.0")
    profiler.start()

    try:
        for cycle, phase in enumerate(PHASES):
            ddup.config(tags={"phase": phase, "cycle": str(cycle)})
            busy_loop(0.5)
            ddup.upload(tracer)
            print(f"uploaded cycle={cycle} phase={phase}", flush=True)
    finally:
        profiler.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
