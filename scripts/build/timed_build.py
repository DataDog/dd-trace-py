#!/usr/bin/env python3
"""Debug-only build timing wrapper for ddtrace.

FOR DEBUGGING BUILD PERFORMANCE ONLY.  Use ``pip install --no-build-isolation
-e .`` for normal development rebuilds.

Wraps ``pip install --no-build-isolation -e .`` and, on completion, parses
the Ninja build log to produce a per-category timing summary written to
``debug_build_metadata.txt`` (or $DD_DEBUG_BUILD_FILE).

The persistent per-Python build environment (cmake, ninja, scikit-build-core,
cython) is managed automatically by ``build_backend.py``; no manual setup is
required.

Usage:
    python scripts/build/timed_build.py [extra pip args...]
    # e.g.
    python scripts/build/timed_build.py -v

Environment variables forwarded to pip (and respected by the build):
    DD_COMPILE_MODE, DD_FAST_BUILD, DD_USE_SCCACHE, DD_SERVERLESS_BUILD
    DD_DEBUG_BUILD_FILE  — output path (default: debug_build_metadata.txt)
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess  # nosec B404
import sys
import time


# ── pip phase detection ───────────────────────────────────────────────────────
# pip -v prints lines like "  Running command <phase description>".
# With --no-build-isolation the "installing build/backend dependencies" phases
# are absent; only prepare_metadata, cmake_build, and wheel_install remain.

_PHASE_MARKERS = [
    # name                  substring to match in pip -v output (lowercased)
    ("pip_start", None),  # synthetic — set at process start
    ("prepare_metadata", "running command preparing editable metadata"),
    ("cmake_build", "running command building editable for"),
    ("wheel_install", "installing collected packages: ddtrace"),
]


def _detect_phase(line: str) -> str | None:
    line_lower = line.lower()
    for name, marker in _PHASE_MARKERS[1:]:  # skip synthetic pip_start
        if marker and marker.lower() in line_lower:
            return name
    return None


def _stream_and_timestamp(proc: subprocess.Popen, phase_times: dict) -> None:
    """Read proc stdout+stderr, print each line, and record phase timestamps."""
    assert proc.stdout is not None  # nosec B101
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        print(line, flush=True)
        phase = _detect_phase(line)
        if phase and phase not in phase_times:
            phase_times[phase] = time.monotonic()


# ── Ninja log parser ──────────────────────────────────────────────────────────


def _parse_ninja_log(log_path: Path) -> list[tuple[int, int, str]]:
    """Return list of (start_ms, end_ms, output) from a .ninja_log file."""
    entries = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            try:
                start_ms, end_ms = int(parts[0]), int(parts[1])
                output = parts[3]
                entries.append((start_ms, end_ms, output))
            except ValueError:
                continue
    return entries


def _categorise(output: str) -> str:
    """Map a Ninja output path to a human-readable build category."""
    o = output.lower()
    if "_deps/absl" in o:
        return "abseil"
    if "corrosion" in o or "cargo-build" in o or "cargo/" in o or "lib_native" in o or "_native." in o:
        return "rust (_native)"
    if "iast_native" in o or "_taint_tracking" in o or "iastpatch" in o:
        return "iast_native (C++)"
    if "dd_wrapper" in o:
        return "dd_wrapper"
    if "_ddup" in o:
        return "_ddup"
    if "_stack" in o:
        return "_stack"
    if "_memalloc" in o:
        return "_memalloc"
    if "_threads" in o:
        return "_threads"
    if "_stacktrace" in o:
        return "_stacktrace"
    if "ddwaf" in o:
        return "ddwaf (download/copy)"
    if "psutil" in o:
        return "psutil"
    if "cython/" in o or ".pyx" in o or "cython_" in o:
        return "cython extensions"
    _cython_so_prefixes = (
        "_encoding.",
        "_tagset.",
        "_threading.",
        "_task.",
        "_exception.",
        "_fast_poisson.",
        "_sampler.",
        "_lock.",
        "metrics_namespaces.",
    )
    if any(Path(output).name.startswith(p) for p in _cython_so_prefixes):
        return "cython extensions"
    return "other"


def _summarise(entries: list[tuple[int, int, str]]) -> dict[str, int]:
    """Compute wall-clock span (ms) per category."""
    spans: dict[str, tuple[int, int]] = {}
    for start_ms, end_ms, output in entries:
        cat = _categorise(output)
        if cat not in spans:
            spans[cat] = (start_ms, end_ms)
        else:
            spans[cat] = (min(spans[cat][0], start_ms), max(spans[cat][1], end_ms))
    return {cat: max(0, end - start) for cat, (start, end) in spans.items()}


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent.parent
    output_file = Path(os.getenv("DD_DEBUG_BUILD_FILE", "debug_build_metadata.txt"))
    pip_args = sys.argv[1:]

    # Always run with -v so phase markers appear in output.
    if "-v" not in pip_args and "--verbose" not in pip_args:
        pip_args = ["-v"] + pip_args

    # Use --no-build-isolation so cmake/ninja from the persistent build env
    # (managed by build_backend.py) are reused at stable paths.
    if "--no-build-isolation" not in pip_args:
        pip_args = ["--no-build-isolation"] + pip_args

    cmd = [sys.executable, "-m", "pip", "install", "-e", str(repo_root)] + pip_args
    print(f"[timed_build] Running: {' '.join(cmd)}\n")

    phase_times: dict[str, float] = {}
    wall_start = time.monotonic()
    wall_start_epoch = time.time()
    phase_times["pip_start"] = wall_start

    proc = subprocess.Popen(  # nosec B603
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=os.environ.copy(),
    )
    _stream_and_timestamp(proc, phase_times)
    proc.wait()
    wall_elapsed = time.monotonic() - wall_start

    # ── Phase breakdown ───────────────────────────────────────────────────────
    ordered_phases = [name for name, _ in _PHASE_MARKERS if name in phase_times]
    phase_durations: list[tuple[str, float]] = []
    for i, name in enumerate(ordered_phases):
        next_ts = phase_times[ordered_phases[i + 1]] if i + 1 < len(ordered_phases) else wall_start + wall_elapsed
        phase_durations.append((name, next_ts - phase_times[name]))

    _labels = {
        "pip_start": "pip startup + build-env check",
        "prepare_metadata": "prepare editable metadata (cmake configure #1)",
        "cmake_build": "cmake configure + ninja build",
        "wheel_install": "wheel unpack + install",
    }

    env_info = {
        "DD_COMPILE_MODE": os.getenv("DD_COMPILE_MODE", "RelWithDebInfo (default)"),
        "DD_FAST_BUILD": os.getenv("DD_FAST_BUILD", "OFF"),
        "DD_USE_SCCACHE": os.getenv("DD_USE_SCCACHE", "OFF"),
        "DD_SERVERLESS_BUILD": os.getenv("DD_SERVERLESS_BUILD", "OFF"),
        "CARGO_BUILD_JOBS": os.getenv("CARGO_BUILD_JOBS", "unset"),
        "CMAKE_BUILD_PARALLEL_LEVEL": os.getenv("CMAKE_BUILD_PARALLEL_LEVEL", "unset"),
    }

    lines = []
    lines.append(f"Total wall time: {wall_elapsed:.2f}s")
    lines.append(f"Exit code: {proc.returncode}")
    lines.append("")
    lines.append("Environment:")
    for k, v in env_info.items():
        lines.append(f"    {k}: {v}")
    lines.append("")
    lines.append("Phase breakdown:")
    for name, dur in phase_durations:
        label = _labels.get(name, name)
        pct = dur / wall_elapsed * 100
        lines.append(f"    {label}: {dur:.2f}s ({pct:.1f}%)")

    # ── Ninja log breakdown ───────────────────────────────────────────────────
    build_dir = repo_root / "build"
    logs = sorted(build_dir.glob("cmake-*/.ninja_log"), key=lambda p: p.stat().st_mtime, reverse=True)

    if logs:
        log_path = logs[0]
        lines.append("")
        lines.append(f"Ninja log: {log_path.relative_to(repo_root)}")
        log_mtime = log_path.stat().st_mtime
        log_was_touched = log_mtime >= wall_start_epoch
        entries = _parse_ninja_log(log_path)
        if not log_was_touched:
            lines.append("  (log unchanged — all targets up-to-date, full incremental cache hit)")
        else:
            totals = _summarise(entries)
            total_wall_ms = max(e for _, e, _ in entries) - min(s for s, _, _ in entries)
            if total_wall_ms / 1000 > wall_elapsed * 1.3:
                lines.append(
                    f"  Partial rebuild (log span {total_wall_ms / 1000:.0f}s >> "
                    f"wall {wall_elapsed:.0f}s — only fast targets ran this build; "
                    f"breakdown reflects prior full-build log data)"
                )
            else:
                lines.append(f"  Ninja build wall span: {total_wall_ms / 1000:.2f}s")
                lines.append("  Build category wall-clock spans (% of ninja span):")
                for cat, ms in sorted(totals.items(), key=lambda x: x[1], reverse=True):
                    pct = (ms / total_wall_ms * 100) if total_wall_ms else 0
                    lines.append(f"      {cat}: {ms / 1000:.2f}s ({pct:.1f}%)")
    else:
        lines.append("No .ninja_log found (clean build dir or cmake not reached).")

    summary = "\n".join(lines)
    print(f"\n[timed_build] Build summary:\n{summary}")
    output_file.write_text(summary + "\n")
    print(f"[timed_build] Summary written to {output_file}")

    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
