/*
 * probe_native.c - native benchmark cores for the CPU-asymmetry probe.
 *
 * EXPERIMENT (do not merge): CPU-asymmetry probe on benchmarking hosts
 * (PR #20052 / APMSP-4059). probe.py compiles this file with `cc -O2`
 * and runs it pinned to one CPU at a time; if the toolchain or build
 * fails, probe.py falls back to its pure-Python cores.
 *
 * Cores (one JSON line per rep on stdout):
 *   int      tight xorshift64 arithmetic loop, no allocation
 *   simd     bulk memcpy on SIZE-byte blocks (default 8 MiB)
 *   stream   bulk memcpy on SIZE-byte blocks (probe.py passes > L3 sizes)
 *   fault    mmap fresh anonymous 1 MiB chunks and touch every page
 *   latency  pointer-chase through a SIZE-byte shuffled u32 permutation
 *
 * Usage: probe_native SCENARIO SECONDS REPS [SIZE]
 * Exit codes: 2 unknown scenario, 3 core mechanism failed (probe.py then
 * records the scenario as unavailable instead of trusting partial data).
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>

static double
now_s(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

static uint64_t rng_state = 0x243F6A8885A308D3ULL;
static uint64_t
rng_next(void)
{
    uint64_t x = rng_state;
    x ^= x << 13;
    x ^= x >> 7;
    x ^= x << 17;
    rng_state = x;
    return x;
}

/* keep the benchmark result observable without a warning-prone local */
static volatile uint64_t g_sink64;
static volatile uint32_t g_sink32;

static void
report(int rep, uint64_t ops, double seconds)
{
    printf("{\"rep\":%d,\"ops\":%llu,\"seconds\":%.6f}\n", rep, (unsigned long long)ops, seconds);
    fflush(stdout);
}

static void
run_int(double seconds, int rep)
{
    uint64_t x = 0x9E3779B97F4A7C15ULL, ops = 0;
    double start = now_s(), deadline = start + seconds;
    for (;;) {
        /* batch so the clock read stays off the measured hot path */
        for (int i = 0; i < 4096; i++) {
            x ^= x << 13;
            x ^= x >> 7;
            x ^= x << 17;
        }
        ops += 4096;
        if (now_s() >= deadline)
            break;
    }
    g_sink64 = x;
    report(rep, ops, now_s() - start);
}

static void
run_memcpy(double seconds, size_t size, int rep)
{
    unsigned char* src = malloc(size);
    unsigned char* dst = malloc(size);
    uint64_t ops = 0;
    double start, deadline;
    if (!src || !dst) {
        fprintf(stderr, "malloc %zu failed\n", size);
        exit(3);
    }
    /* touch both outside the timed window so copies fault nothing */
    memset(src, 0x5A, size);
    memset(dst, 0, size);
    start = now_s();
    deadline = start + seconds;
    for (;;) {
        memcpy(dst, src, size);
        ops += (uint64_t)size;
        if (now_s() >= deadline)
            break;
    }
    free(src);
    free(dst);
    report(rep, ops, now_s() - start);
}

static void
run_fault(double seconds, int rep)
{
    const size_t chunk = 1u << 20;
    const size_t page = 1u << 12;
    uint64_t ops = 0;
    double start = now_s(), deadline = start + seconds;
    for (;;) {
        unsigned char* p = mmap(NULL, chunk, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        size_t off;
        if (p == MAP_FAILED) {
            fprintf(stderr, "mmap failed\n");
            exit(3);
        }
        for (off = 0; off < chunk; off += page)
            p[off] = 1;
        munmap(p, chunk);
        ops += chunk / page;
        if (now_s() >= deadline)
            break;
    }
    report(rep, ops, now_s() - start);
}

/*
 * Latency note: the table is mmap'd and explicitly MADV_NOHUGEPAGE'd.
 * Without that, the first rep chases 4K pages and later reps 2M transparent
 * hugepages once khugepaged collapses the region, which alone produced the
 * ~2x rep-to-rep spread seen on the benchmarking hosts (uniform across all
 * CPUs, so pure measurement artifact, not asymmetry). The untimed warm-up
 * pass keeps page-table/first-touch state out of rep 0 as well.
 */
static void
run_latency(double seconds, size_t bytes, int rep)
{
    size_t n = bytes / 4, i, idx = 0;
    uint32_t* tbl = mmap(NULL, bytes, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    uint64_t ops = 0;
    double start, deadline;
    if (tbl == MAP_FAILED) {
        fprintf(stderr, "mmap %zu failed\n", bytes);
        exit(3);
    }
#ifdef MADV_NOHUGEPAGE
    madvise(tbl, bytes, MADV_NOHUGEPAGE);
#endif
    if (n < 2) {
        fprintf(stderr, "table too small: %zu\n", bytes);
        exit(3);
    }
    for (i = 0; i < n; i++)
        tbl[i] = (uint32_t)i;
    /* Sattolo's shuffle: j < i yields exactly one cycle over the whole
     * table, so every rep chases the same-size full-DRAM circuit. Plain
     * Fisher-Yates instead leaves the cycle containing index 0 at a
     * uniformly random length (measured across reps: 0.1% to 98% of the
     * table), which let whole reps sit in cache and alone caused the huge
     * rep-to-rep spread of earlier probe versions. */
    for (i = n - 1; i > 0; i--) {
        size_t j = (size_t)(rng_next() % i);
        uint32_t t = tbl[i];
        tbl[i] = tbl[j];
        tbl[j] = t;
    }
    for (i = 0; i < (1u << 22); i++) /* untimed warm-up */
        idx = tbl[idx];
    start = now_s();
    deadline = start + seconds;
    for (;;) {
        for (int k = 0; k < 256; k++)
            idx = tbl[idx];
        ops += 256;
        if (now_s() >= deadline)
            break;
    }
    g_sink32 = tbl[idx];
    munmap(tbl, bytes);
    report(rep, ops, now_s() - start);
}

int
main(int argc, char** argv)
{
    const char* scenario;
    double seconds;
    int reps, rep;
    size_t size;
    if (argc < 4) {
        fprintf(stderr, "usage: probe_native SCENARIO SECONDS REPS [SIZE]\n");
        return 2;
    }
    scenario = argv[1];
    seconds = strtod(argv[2], NULL);
    reps = (int)strtol(argv[3], NULL, 10);
    size = argc > 4 ? (size_t)strtoull(argv[4], NULL, 10) : 0;
    if (seconds <= 0 || reps <= 0) {
        fprintf(stderr, "SECONDS and REPS must be positive\n");
        return 2;
    }
    for (rep = 0; rep < reps; rep++) {
        if (strcmp(scenario, "int") == 0) {
            run_int(seconds, rep);
        } else if (strcmp(scenario, "simd") == 0) {
            run_memcpy(seconds, size ? size : (size_t)8 << 20, rep);
        } else if (strcmp(scenario, "stream") == 0) {
            run_memcpy(seconds, size ? size : (size_t)256 << 20, rep);
        } else if (strcmp(scenario, "fault") == 0) {
            run_fault(seconds, rep);
        } else if (strcmp(scenario, "latency") == 0) {
            run_latency(seconds, size ? size : (size_t)256 << 20, rep);
        } else {
            fprintf(stderr, "unknown scenario %s\n", scenario);
            return 2;
        }
    }
    return 0;
}
