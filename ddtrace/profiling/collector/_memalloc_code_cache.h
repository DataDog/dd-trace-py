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

/* Result of a cache lookup.
 * func_id == nullptr: cache miss — caller must do a full miss (intern strings, insert).
 * line == -1: lasti didn't match the stored value — caller must resolve the line for this frame.
 * line >= 0: lasti matched; cached line number is valid.
 *
 * CacheHit is intentionally kept small to make lookup() cheap to return by value.
 * On x86-64 System V, aggregates up to 16B are typically returned in registers; other ABIs may differ. */
struct CacheHit
{
    Datadog::function_id func_id; // nullptr = miss
    int line;                     // -1 = lasti mismatch; >=0 = cached line
};

/* Keep CacheHit small; increasing its size can force some ABIs to use a hidden
 * return pointer (sret), adding per-lookup overhead on the allocator-hook hot path. */
static_assert(sizeof(CacheHit) <= 16,
              "CacheHit exceeds 16B — consider measuring lookup() "
              "return overhead on supported ABIs before adding fields");

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
 * Address reuse: the key is a raw PyCodeObject* and CPython may free a
 * code object and reassign its address to a new one. Each entry therefore
 * also stores the code object's identity (name/filename/firstlineno) at
 * insert time; lookup returns a hit only if that identity still matches
 * the live code object, so a reused address is treated as a miss instead
 * of misattributing the frame. The caller supplies the identity (it holds
 * the live object), so the cache never dereferences a stored pointer.
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
    /* WAYS_PER_SET: number of cache slots per set, i.e. the associativity.
     * 2-way means each set has 2 entries that are linearly scanned on lookup. */
    static constexpr size_t WAYS_PER_SET = 2;
    static constexpr size_t DEFAULT_CAPACITY = 1024;
    static constexpr size_t MIN_CAPACITY = 64;
    static constexpr size_t MAX_CAPACITY = 1 << 20; // 1M cap as a sanity ceiling

    /* capacity_hint is rounded up so num_sets is a power of two and total
     * capacity = num_sets * WAYS_PER_SET. Clamped to [MIN, MAX]. */
    explicit CodeFunctionCache(size_t capacity_hint = DEFAULT_CAPACITY);

    /* Returns a CacheHit for code only if present AND its stored identity
     * still matches the supplied (name, filename, firstlineno), guarding
     * against PyCodeObject address reuse. Check hit.func_id != nullptr for a
     * hit; hit.line >= 0 means lasti matched the stored value so the caller
     * can skip parse_linetable. */
    CacheHit lookup(PyCodeObject* code, PyObject* name, PyObject* filename, int firstlineno, int lasti) noexcept;

    /* Inserts (code, id) together with the identity used to validate future
     * lookups plus the (lasti, line) pair. If the target set is full, evicts
     * via FIFO. */
    void insert(PyCodeObject* code,
                Datadog::function_id id,
                PyObject* name,
                PyObject* filename,
                int firstlineno,
                int lasti,
                int line);

    /* Drops every entry. */
    void clear();

    /* Process-wide singleton, mirrors heap_tracker_t::instance. */
    static CodeFunctionCache* instance;

  private:
    struct Set
    {
        PyCodeObject* codes[WAYS_PER_SET] = { nullptr, nullptr };
        Datadog::function_id functions[WAYS_PER_SET] = { nullptr, nullptr };
        /* Identity captured at insert, compared on lookup to detect address
         * reuse. Stored as opaque values and never dereferenced, so a stale
         * pointer is safe to compare. */
        PyObject* names[WAYS_PER_SET] = { nullptr, nullptr };
        PyObject* filenames[WAYS_PER_SET] = { nullptr, nullptr };
        int firstlines[WAYS_PER_SET] = { 0, 0 };
        int lastis[WAYS_PER_SET] = { -1, -1 }; // -1 = no lasti cached
        int lines[WAYS_PER_SET] = { 0, 0 };
        /* FIFO eviction: next way to overwrite, alternates 0/1. */
        uint8_t next_evict = 0;
    };

    std::vector<Set> sets_;
    uint8_t log2_set_bits_; // log2(num_sets); num_sets is a power of two in [32, 1<<19], so this is in [5, 19]

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
