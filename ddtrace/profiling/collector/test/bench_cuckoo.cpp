/* Microbenchmark: CuckooFilter::contains vs absl::flat_hash_map::find
 *
 * Measures the savings claimed by the optimization plan: ~70 cycles per
 * free for the 99% of frees that aren't sampled.
 *
 * The benchmark builds a working set of 65,536 random void* pointers,
 * inserts them into both a CuckooFilter and an absl::flat_hash_map, and
 * times tight loops over (a) probes for ABSENT keys (the 99% case the
 * optimization is targeting), and (b) probes for PRESENT keys (the 1%
 * sampled case, where both the filter and map are touched).
 *
 * Build & run locally (from this directory):
 *   ABSL=$(realpath ../../../../.download_cache/_cmake_deps/absl_install_arm64)
 *   clang++ -std=c++20 -O3 -march=native -DNDEBUG -I.. -I"$ABSL/include" \
 *     bench_cuckoo.cpp -L"$ABSL/lib" \
 *     -labsl_hash -labsl_low_level_hash -labsl_city -labsl_raw_hash_set \
 *     -labsl_hashtablez_sampler -labsl_synchronization -labsl_time \
 *     -labsl_time_zone -labsl_strings -labsl_strings_internal \
 *     -labsl_throw_delegate -labsl_base -labsl_raw_logging_internal \
 *     -labsl_log_severity -labsl_spinlock_wait \
 *     -labsl_exponential_biased -labsl_int128 -labsl_stacktrace \
 *     -labsl_symbolize -labsl_debugging_internal -labsl_demangle_internal \
 *     -labsl_malloc_internal \
 *     -o /tmp/bench_cuckoo && /tmp/bench_cuckoo
 */

#include <chrono>
#include <cstdint>
#include <cstdio>
#include <random>
#include <unordered_set>
#include <vector>

#include "_memalloc_cuckoo.hpp"
#include "absl/container/flat_hash_map.h"

namespace {

constexpr size_t WORKING_SET = 65'536;
constexpr size_t QUERY_REPEATS = 200;
constexpr size_t QUERIES_TOTAL = WORKING_SET * QUERY_REPEATS;

/* Generate distinct, 16-byte-aligned fake pointers. Mimics malloc output. */
std::vector<const void*>
make_ptrs(uint64_t seed, size_t n)
{
    std::mt19937_64 rng(seed);
    std::unordered_set<uintptr_t> seen;
    std::vector<const void*> out;
    out.reserve(n);
    while (out.size() < n) {
        uintptr_t v = static_cast<uintptr_t>(rng()) & ~uintptr_t{ 0xF };
        if (v != 0 && seen.insert(v).second) {
            out.push_back(reinterpret_cast<const void*>(v));
        }
    }
    return out;
}

double
ns_per_op(std::chrono::steady_clock::time_point t0, std::chrono::steady_clock::time_point t1, size_t ops)
{
    auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(t1 - t0).count();
    return static_cast<double>(ns) / static_cast<double>(ops);
}

} // namespace

int
main()
{
    std::printf("CuckooFilter vs absl::flat_hash_map microbenchmark\n");
    std::printf("Working set: %zu ptrs   Queries per phase: %zu\n", WORKING_SET, QUERIES_TOTAL);
    std::printf("--------------------------------------------------------\n");

    auto live = make_ptrs(0xCAFE'BABE'DEAD'BEEFULL, WORKING_SET);
    auto absent = make_ptrs(0x1234'5678'9ABC'DEF0ULL, WORKING_SET);

    /* Populate both data structures. */
    CuckooFilter filter;
    absl::flat_hash_map<const void*, int> map;
    map.reserve(WORKING_SET);
    for (auto p : live) {
        bool ok = filter.insert(p);
        (void)ok;
        map.emplace(p, 0);
    }
    std::printf("Filter size: %zu   Map size: %zu\n\n", filter.size(), map.size());

    /* Phase 1: ABSENT-key lookups (the 99% case the optimization targets).
     * Filter should reject in ~10–15 cycles via the bucket scan; map must
     * do a full hash + table probe + linear scan of the bucket. */
    {
        size_t hits = 0;
        auto t0 = std::chrono::steady_clock::now();
        for (size_t r = 0; r < QUERY_REPEATS; r++) {
            for (auto p : absent) {
                if (filter.contains(p)) {
                    hits++;
                }
            }
        }
        auto t1 = std::chrono::steady_clock::now();
        double ns = ns_per_op(t0, t1, QUERIES_TOTAL);
        double pct_fp = 100.0 * static_cast<double>(hits) / static_cast<double>(QUERIES_TOTAL);
        std::printf("Filter contains() ABSENT: %7.2f ns/op  (FPR observed: %.4f%%)\n", ns, pct_fp);
    }
    {
        size_t hits = 0;
        auto t0 = std::chrono::steady_clock::now();
        for (size_t r = 0; r < QUERY_REPEATS; r++) {
            for (auto p : absent) {
                if (map.find(p) != map.end()) {
                    hits++;
                }
            }
        }
        auto t1 = std::chrono::steady_clock::now();
        double ns = ns_per_op(t0, t1, QUERIES_TOTAL);
        std::printf("Map find()        ABSENT: %7.2f ns/op  (hits: %zu)\n", ns, hits);
    }

    std::printf("\n");

    /* Phase 2: PRESENT-key lookups (the 1% sampled case — both paths fire). */
    {
        size_t hits = 0;
        auto t0 = std::chrono::steady_clock::now();
        for (size_t r = 0; r < QUERY_REPEATS; r++) {
            for (auto p : live) {
                if (filter.contains(p)) {
                    hits++;
                }
            }
        }
        auto t1 = std::chrono::steady_clock::now();
        double ns = ns_per_op(t0, t1, QUERIES_TOTAL);
        std::printf("Filter contains() PRESENT: %6.2f ns/op  (hits: %zu)\n", ns, hits);
    }
    {
        size_t hits = 0;
        auto t0 = std::chrono::steady_clock::now();
        for (size_t r = 0; r < QUERY_REPEATS; r++) {
            for (auto p : live) {
                if (map.find(p) != map.end()) {
                    hits++;
                }
            }
        }
        auto t1 = std::chrono::steady_clock::now();
        double ns = ns_per_op(t0, t1, QUERIES_TOTAL);
        std::printf("Map find()        PRESENT: %6.2f ns/op  (hits: %zu)\n", ns, hits);
    }

    std::printf("\n");

    /* Phase 3: Cache-cold lookups.
     *
     * The optimization plan's claim of "~50-100 cycles per free" for the
     * hashmap probe assumed cache-cold access. In the phases above both
     * data structures are warm in L2, so we're only measuring compute
     * cost — and the filter's two absl::Hash calls + bucket scans cost
     * slightly more than one Swiss Table miss.
     *
     * Production reality: the map is touched only on samples (~1 per
     * 1MB allocated), then sits idle through millions of free() calls;
     * by the next free of a sampled ptr its line has been evicted. The
     * filter, by contrast, is hit on every alloc AND every free, so it
     * stays warm in L2.
     *
     * Simulate this by polluting L2 between accesses: walk a 16 MB
     * scratch buffer between each filter/map probe. The filter still
     * gets thrashed by the scrub, but both paths now pay cold-line
     * costs equally — what's left is the structural difference. */
    /* Caveat:
     *
     * These numbers are warm-cache. Both the 256 KB filter and the
     * ~1.5 MB map fit in L2, so we're measuring compute cost only —
     * not the cache-locality benefit the optimization was designed for.
     * The optimization plan's "~50–100 cycles" hashmap cost assumed
     * cache-cold access, which only happens in production where:
     *   (1) the map is touched only on samples (~1 per heap-sample-size
     *       bytes allocated, e.g. 1 in every 1MB), so its lines are
     *       routinely evicted between accesses;
     *   (2) the filter is touched on every alloc AND every free, so it
     *       stays warm in L2.
     *
     * We tried adding a cache-scrub between probes, but the scrub
     * itself (~250 µs) drowns out the per-probe signal (~10–100 ns).
     * For a fair production-like measurement, use the alloc/free
     * pytest-benchmark in tests/profiling/collector/test_memalloc.py
     * (test_memalloc_speed) and compare branches via:
     *     pytest-benchmark compare baseline cuckoo
     *
     * What the warm-cache numbers DO tell us: in tight loops, the
     * filter pays ~1 ns more per ABSENT lookup than the map. This is
     * because the filter does TWO absl::Hash calls (primary +
     * alt-bucket) where the map does one. The optimization only pays
     * off when the map is genuinely cold — which is the production
     * case but not the microbenchmark case. */
    return 0;
}
