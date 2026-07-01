#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "sample.hpp"

#include <cstddef>
#include <cstdint>
#include <vector>

namespace Datadog {

/* Result of a cache lookup.
 * func_id == nullptr: miss — caller must intern strings and insert.
 * func_id != nullptr: hit — func_id is valid; line >= 0 means the cached line
 *   number is also valid (lasti matched) so parse_linetable can be skipped;
 *   line == -1 means the caller must resolve the line number itself.
 *
 * CacheResult is intentionally kept small to make lookup() cheap to return by value.
 * On x86-64 System V, aggregates up to 16B are typically returned in registers; other ABIs may differ. */
struct CacheResult
{
    Datadog::function_id func_id; // nullptr = miss
    int line;                     // -1 = lasti mismatch; >=0 = cached line (skip parse_linetable)
};

/* Keep CacheResult small; increasing its size can force some ABIs to use a hidden
 * return pointer (sret), adding per-lookup overhead on the allocator-hook hot path. */
static_assert(sizeof(CacheResult) <= 16,
              "CacheResult exceeds 16B — consider measuring lookup() "
              "return overhead on supported ABIs before adding fields");

/* CodeFunctionCache caches libdatadog function_id values keyed by PyCodeObject*.
 * Frame walks during heap-profiler sample construction call ProfilesDictionary::insert_str
 * twice and insert_function once per frame, which dominate profiler-side CPU on workloads
 * with repetitive stacks. The cache short-circuits those three libdd calls for any frame
 * whose code object has been seen before.
 *
 * Implementation: 2-way set-associative cache with Fibonacci (golden-ratio) set-index
 * hashing. A code object pointer is hashed via (ptr * 0x9E3779B97F4A7C15) >> (64 - log2 sets)
 * to pick a set; within the set, at most WAYS_PER_SET = 2 slots are checked linearly.
 * Eviction is FIFO per set. The vector is allocated once at construction — no heap
 * allocations occur during lookup or insert.
 *
 * Allocation safety: absl::flat_hash_map and std::unordered_map are not used here
 * because their internal allocations would re-enter our PyObject_Malloc /
 * PyMem_RawMalloc hooks, causing infinite recursion. The pre-allocated vector is safe.
 *
 * Address reuse: the key is a raw PyCodeObject* and CPython may free a code object and
 * reassign its address to a new one. Each entry therefore also stores the code object's
 * identity (name/filename/firstlineno) at insert time; lookup returns a hit only if that
 * identity still matches the live code object, so a reused address is treated as a miss
 * instead of misattributing the frame.
 *
 * Concurrency: heap-profiler hooks run under the GIL; no internal locking needed.
 *
 * Lifetime: cleared on postfork_child and profiler stop/restart. libdd function_ids do not
 * expire while their owning ProfilesDictionary lives, which spans the whole profiler
 * lifetime (verified in _memalloc_heap.cpp -- allocs_m holds samples whose locations
 * reference function_ids and only clears at postfork_child).
 *
 * Two-tier hit: each entry also stores the last-seen lasti (bytecode offset) and the
 * resolved line number for that offset. When lookup() finds a matching entry and the
 * supplied lasti equals the stored value, it returns the cached line number in
 * CacheResult::line (>=0), letting the caller skip parse_linetable entirely. When lasti
 * differs, line is returned as -1 and the caller falls back to memalloc_resolve_lineno.
 */
class CodeFunctionCache
{
  public:
    static constexpr size_t DEFAULT_CAPACITY = 1024;
    static constexpr size_t MIN_CAPACITY = 64;
    static constexpr size_t MAX_CAPACITY = 1 << 20; // 1M cap as a sanity ceiling
    static constexpr size_t WAYS_PER_SET = 2;

    explicit CodeFunctionCache(size_t capacity_hint);

    /* Returns a CacheResult for code only if present AND its stored identity still matches
     * the supplied (name, filename, firstlineno), guarding against PyCodeObject address reuse.
     * Check result.func_id != nullptr for a hit. On a hit, result.line >= 0 when lasti
     * matches the stored value (parse_linetable skipped); result.line == -1 otherwise. */
    CacheResult lookup(PyCodeObject* code, PyObject* name, PyObject* filename, int firstlineno, int lasti) noexcept;

    /* Inserts (code, id) with the identity and lasti→line used to validate future lookups.
     * Evicts the FIFO victim in the target set if both slots are occupied. */
    void insert(PyCodeObject* code,
                Datadog::function_id id,
                PyObject* name,
                PyObject* filename,
                int firstlineno,
                int lasti,
                int line);

    /* Drops every entry, retaining allocated capacity. */
    void clear();

    /* Process-wide singleton, mirrors heap_tracker_t::instance. */
    static CodeFunctionCache* instance;

  private:
    struct Set
    {
        PyCodeObject* codes[2] = { nullptr, nullptr };
        Datadog::function_id functions[2] = { nullptr, nullptr };
        PyObject* names[2] = { nullptr, nullptr };
        PyObject* filenames[2] = { nullptr, nullptr };
        int firstlines[2] = { 0, 0 };
        int lastis[2] = { -1, -1 };
        int lines[2] = { 0, 0 };
        uint8_t next_evict = 0;
    };

    std::vector<Set> sets_;
    uint8_t log2_set_bits_;

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
