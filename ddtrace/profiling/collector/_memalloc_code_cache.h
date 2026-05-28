#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "sample.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <vector>

namespace Datadog {

/* CodeFunctionCache caches libdatadog function_id values keyed by
 * PyCodeObject*. Frame walks during heap-profiler sample construction
 * call ProfilesDictionary::insert_str twice and insert_function once
 * per frame, which dominate profiler-side CPU on workloads with
 * repetitive stacks. The cache short-circuits those three libdd calls
 * for any frame whose code object has been seen before.
 *
 * Organization: 4-way set-associative. Sets are indexed by a hash of
 * the PyCodeObject pointer; within a set, ways are linearly scanned.
 * On a set-full insert, CLOCK / Second-Chance eviction picks the least
 * recently used way (single bit per way).
 *
 * Concurrency: heap-profiler hooks run under the GIL. The singleton
 * is invoked single-threaded by construction; no internal locking.
 *
 * Lifetime: cleared on postfork_child and profiler stop/restart. libdd
 * function_ids do not expire while their owning ProfilesDictionary
 * lives, which spans the whole profiler lifetime (verified in
 * _memalloc_heap.cpp -- allocs_m holds samples whose locations
 * reference function_ids and only clears at postfork_child).
 */
class CodeFunctionCache
{
  public:
    static constexpr size_t WAYS_PER_SET = 4;
    static constexpr size_t DEFAULT_CAPACITY = 32768;
    static constexpr size_t MIN_CAPACITY = 64;
    static constexpr size_t MAX_CAPACITY = 1 << 20; // 1M cap as a sanity ceiling

    /* `capacity_hint` is rounded up so num_sets is a power of two and total
     * capacity = num_sets * WAYS_PER_SET. Clamped to [MIN, MAX]. */
    explicit CodeFunctionCache(size_t capacity_hint = DEFAULT_CAPACITY);

    /* Returns cached function_id if present; marks entry recently-used. */
    std::optional<Datadog::function_id> lookup(PyCodeObject* code);

    /* Inserts (code, id). If the target set is full, evicts the least
     * recently used way via CLOCK. */
    void insert(PyCodeObject* code, Datadog::function_id id);

    /* Drops every entry. Counters are preserved (use reset_counters to
     * zero them). */
    void clear();

    /* Telemetry. Reads are unsynchronized and intentionally racy (single-
     * threaded under GIL anyway); reset_counters() zeros all three. */
    uint64_t hits() const { return hits_; }
    uint64_t misses() const { return misses_; }
    uint64_t evictions() const { return evictions_; }
    void reset_counters();

    size_t capacity() const { return sets_.size() * WAYS_PER_SET; }

    /* Debug: histogram[k] = number of sets currently holding exactly k
     * entries. Sum equals num_sets. O(num_sets); never on hot path. */
    std::array<size_t, WAYS_PER_SET + 1> occupancy_histogram() const;

    /* Process-wide singleton, mirrors heap_tracker_t::instance. */
    static CodeFunctionCache* instance;

  private:
    struct Set
    {
        PyCodeObject* codes[WAYS_PER_SET] = { nullptr, nullptr, nullptr, nullptr };
        Datadog::function_id functions[WAYS_PER_SET] = { nullptr, nullptr, nullptr, nullptr };
        /* One bit per way in the low nibble. Bit i = ways[i] recently used. */
        uint8_t recently_used_mask = 0;
        /* Next way to consider for eviction, in [0, WAYS_PER_SET). */
        uint8_t clock_hand = 0;
    };

    std::vector<Set> sets_;
    uint8_t log2_set_bits_; // log2(num_sets); num_sets is a power of two in [16, 1<<18]

    uint64_t hits_ = 0;
    uint64_t misses_ = 0;
    uint64_t evictions_ = 0;

    size_t set_index(PyCodeObject* code) const;
    static bool way_recently_used(uint8_t mask, size_t way) { return (mask >> way) & 1u; }
    static uint8_t set_way_used(uint8_t mask, size_t way) { return mask | static_cast<uint8_t>(1u << way); }
    static uint8_t clear_way_used(uint8_t mask, size_t way) { return mask & static_cast<uint8_t>(~(1u << way)); }
};

/* Public API for the heap profiler. Init reads
 * DD_PROFILING_MEMALLOC_CODE_CACHE_SIZE from the environment and creates
 * the singleton; deinit destroys it. Both are idempotent and safe to call
 * on an unused-state. */
bool
memalloc_code_cache_init();
void
memalloc_code_cache_deinit();
void
memalloc_code_cache_clear();

} // namespace Datadog
