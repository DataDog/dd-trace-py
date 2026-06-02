"""Measure native malloc churn during sustained unwind_greenlets sampling.

On a single sampling thread, allocate-then-free each sample reuses the same
freed blocks, so RSS/arena are identical between the buffer-reuse fix and
unfixed code. The signal that actually separates them is the *number* of
allocations: unfixed code allocates one StackInfo (std::deque<Frame>) per
tracked greenlet per sample; the fix reuses buffers across samples.

We count allocations with a tiny LD_PRELOAD malloc/calloc/realloc interposer
(black-box; works identically on both builds) and report the delta over a
fixed sampling window.

EXPERIMENT MODE: prints MALLOC_DELTA and always passes, so we can read the
real numbers from CI on both the unfixed (#18422) and fixed builds before
choosing a threshold.
"""

import os
import subprocess
import sys
import textwrap

import pytest


GEVENT_COMPATIBLE_WITH_PYTHON_VERSION = os.getenv("DD_PROFILE_TEST_GEVENT", False) and (
    sys.version_info[:2] < (3, 13) or (sys.version_info[:2] == (3, 13) and sys.version_info[3] != "free-threading")
)

_INTERPOSER_C = r"""
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdatomic.h>
#include <stddef.h>

static atomic_ullong g_cnt;
static void *(*r_malloc)(size_t);
static void *(*r_calloc)(size_t, size_t);
static void *(*r_realloc)(void *, size_t);

/* Serve calloc out of a static buffer while dlsym() bootstraps. */
static char g_tmp[1 << 20];
static size_t g_off;
static int g_initing;

static void mc_init(void) {
    g_initing = 1;
    r_malloc = dlsym(RTLD_NEXT, "malloc");
    r_calloc = dlsym(RTLD_NEXT, "calloc");
    r_realloc = dlsym(RTLD_NEXT, "realloc");
    g_initing = 0;
}

void *malloc(size_t s) {
    if (!r_malloc) mc_init();
    atomic_fetch_add(&g_cnt, 1);
    return r_malloc(s);
}

void *realloc(void *p, size_t s) {
    if (!r_realloc) mc_init();
    atomic_fetch_add(&g_cnt, 1);
    return r_realloc(p, s);
}

void *calloc(size_t n, size_t s) {
    if (g_initing) {
        size_t t = n * s;
        void *p = g_tmp + g_off;
        g_off += (t + 15) & ~((size_t)15);
        return p;
    }
    if (!r_calloc) mc_init();
    atomic_fetch_add(&g_cnt, 1);
    return r_calloc(n, s);
}

unsigned long long mc_get(void) { return atomic_load(&g_cnt); }
"""

# Program run inside the LD_PRELOAD'd subprocess. Prints MALLOC_DELTA=<n>.
_INNER = textwrap.dedent(
    """
    from gevent import monkey
    monkey.patch_all()

    import ctypes
    import time

    import gevent

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler

    me = ctypes.CDLL(None)
    me.mc_get.restype = ctypes.c_ulonglong

    N_IDLE = 2000
    STACK_DEPTH = 50
    WARMUP_S = 1.0
    MEASURE_S = 5.0

    def _idle_deep(depth):
        if depth > 0:
            _idle_deep(depth - 1)
        else:
            gevent.sleep(1000)

    def idle_greenlet():
        _idle_deep(STACK_DEPTH)

    p = profiler.Profiler()
    p.start()
    stack.set_interval(0.005)
    stack.set_adaptive_sampling(False)
    idles = [gevent.spawn(idle_greenlet) for _ in range(N_IDLE)]
    gevent.sleep(WARMUP_S)

    start = me.mc_get()
    t_end = time.monotonic() + MEASURE_S
    n_yields = 0
    while time.monotonic() < t_end:
        gevent.sleep(0.05)
        n_yields += 1
    delta = me.mc_get() - start

    print("MALLOC_DELTA=%d" % delta)
    print("MEASURE_S=%.1f N_IDLE=%d STACK_DEPTH=%d YIELDS=%d" % (MEASURE_S, N_IDLE, STACK_DEPTH, n_yields))

    gevent.killall(idles, timeout=5)
    p.stop()
    """
)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="LD_PRELOAD interposer is Linux-only")
@pytest.mark.skipif(not GEVENT_COMPATIBLE_WITH_PYTHON_VERSION, reason="gevent not compatible")
def test_greenlet_malloc_churn(tmp_path) -> None:
    cc = os.environ.get("CC", "cc")
    src = tmp_path / "mc.c"
    so = tmp_path / "mc.so"
    src.write_text(_INTERPOSER_C)
    try:
        subprocess.run(
            [cc, "-shared", "-fPIC", "-O2", "-o", str(so), str(src), "-ldl"],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as e:
        pytest.skip(f"could not build malloc interposer: {e}")

    env = dict(os.environ)
    env["LD_PRELOAD"] = (str(so) + " " + env.get("LD_PRELOAD", "")).strip()
    env["DD_PROFILING_OUTPUT_PPROF"] = str(tmp_path / "prof")
    # Keep glibc on the main arena so counts are stable and the sampling
    # thread does not get its own arena (irrelevant to malloc *counts*, but
    # keeps behavior deterministic).
    env["MALLOC_ARENA_MAX"] = "1"

    proc = subprocess.run(
        [sys.executable, "-c", _INNER],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    sys.stderr.write(proc.stderr)
    sys.stdout.write(proc.stdout)
    assert proc.returncode == 0, f"inner subprocess failed: {proc.returncode}"
    assert "MALLOC_DELTA=" in proc.stdout
    # EXPERIMENT: do not assert a threshold yet; just surface the number.
