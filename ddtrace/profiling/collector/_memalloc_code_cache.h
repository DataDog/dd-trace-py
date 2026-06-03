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

/* Result of a successful cache lookup. line_valid is true when lasti matched
 * the stored value for this way, meaning the cached line number can be used
 * directly without calling parse_linetable. */
struct CacheHit
{
    Datadog::function_id func_id;
    int line;
    bool line_valid;
};

/* CodeFunctionCache caches libdatadog function_id values keyed by
 * PyCodeObject*. Frame walks during heap-profiler sample construction
 * call ProfilesDictionary::insert_str twice and insert_function once
 * per frame, which dominate profiler-side CPU on workloads with
 * repetitive stacks. The cache short-circuits those three libdd calls
 * for any frame whose code object has been seen before.
 *
 * Organization: 2-way set-associative. Sets are indexed by a hash of
 * the PyCodeObject pointer; within a set, ways are linearly scanned.
 * On a set-full insert, FIFO eviction overwrites the next_evict slot.
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
    static constexpr size_t WAYS_PER_SET = 2;
    static constexpr size_t DEFAULT_CAPACITY = 1024;
    static constexpr size_t MIN_CAPACITY = 64;
    static constexpr size_t MAX_CAPACITY = 1 << 20; // 1M cap as a sanity ceiling

    /* `capacity_hint` is rounded up so num_sets is a power of two and total
     * capacity = num_sets * WAYS_PER_SET. Clamped to [MIN, MAX]. */
    explicit CodeFunctionCache(size_t capacity_hint = DEFAULT_CAPACITY);

    /* Returns CacheHit if code is cached; line_valid is true when lasti
     * matches the stored value so parse_linetable can be skipped. */
    std::optional<CacheHit> lookup(PyCodeObject* code, int lasti);

    /* Inserts (code, id, lasti, line). If the target set is full, evicts via FIFO. */
    void insert(PyCodeObject* code, Datadog::function_id id, int lasti, int line);

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
        PyCodeObject* codes[WAYS_PER_SET] = { nullptr, nullptr };
        Datadog::function_id functions[WAYS_PER_SET] = { nullptr, nullptr };
        int lastis[WAYS_PER_SET] = { -1, -1 }; // -1 = no lasti cached
        int lines[WAYS_PER_SET] = { 0, 0 };
        /* FIFO eviction: next way to overwrite, alternates 0/1. */
        uint8_t next_evict = 0;
    };

    std::vector<Set> sets_;
    uint8_t log2_set_bits_; // log2(num_sets); num_sets is a power of two in [16, 1<<18]

    uint64_t hits_ = 0;
    uint64_t misses_ = 0;
    uint64_t evictions_ = 0;

    size_t set_index(PyCodeObject* code) const;
};

/* Public API for the heap profiler.
 * memalloc_code_cache_init creates the singleton with the given capacity;
 * deinit destroys it. Both are idempotent and safe to call on an unused state. */
bool
memalloc_code_cache_init(size_t capacity);
void
memalloc_code_cache_deinit();
void
memalloc_code_cache_clear();

} // namespace Datadog
