#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "sample.hpp"

#include <cstddef>
#include <cstdint>

/* DoE variant D: always use std::unordered_map as the code-cache container,
 * regardless of build type or whether Abseil is compiled in. This isolates the
 * effect of the container choice (node-based std::unordered_map vs the custom
 * set-associative cache and vs absl::flat_hash_map) on the allocator-hook hot
 * path. The rest of the file (heap map, etc.) is unchanged from variant B. */
#include <unordered_map>
template<typename K, typename V>
using CodeCacheMap = std::unordered_map<K, V>;

namespace Datadog {

/* Result of a cache lookup.
 * func_id == nullptr: full cache miss — line is undefined; caller must intern strings and insert.
 * func_id != nullptr, line == -1: function cached but lasti mismatched — caller must re-parse the line table.
 * func_id != nullptr, line >= 0: full hit — both function_id and line number are valid.
 *
 * line is only meaningful when func_id != nullptr.
 *
 * CacheHit is intentionally kept small to make lookup() cheap to return by value.
 * On x86-64 System V, aggregates up to 16B are typically returned in registers; other ABIs may differ. */
struct CacheHit
{
    Datadog::function_id func_id; // nullptr = miss
    int line;                     // only valid when func_id != nullptr; -1 = lasti mismatch, >=0 = cached line
};

/* Keep CacheHit small; increasing its size can force some ABIs to use a hidden
 * return pointer (sret), adding per-lookup overhead on the allocator-hook hot path. */
static_assert(sizeof(CacheHit) <= 16,
              "CacheHit exceeds 16B — consider measuring lookup() "
              "return overhead on supported ABIs before adding fields");

/* CodeFunctionCache caches libdatadog function_id values keyed by PyCodeObject*.
 * Frame walks during heap-profiler sample construction call ProfilesDictionary::insert_str
 * twice and insert_function once per frame, which dominate profiler-side CPU on workloads
 * with repetitive stacks. The cache short-circuits those three libdd calls for any frame
 * whose code object has been seen before.
 *
 * Implementation (variant D): std::unordered_map with buckets reserved at construction
 * to max_capacity entries. Unlike a flat/open-addressing map, std::unordered_map is
 * node-based, so each insert still heap-allocates a node even after reserve(); only the
 * bucket array is preallocated. When capacity is exceeded, one entry is evicted before
 * inserting the new one, keeping map size bounded.
 *
 * Address reuse: the key is a raw PyCodeObject* and CPython may free a code object and
 * reassign its address to a new one. Each entry therefore also stores the code object's
 * identity (name/filename/firstlineno) at insert time; lookup returns a hit only if that
 * identity still matches the live code object, so a reused address is treated as a miss
 * instead of misattributing the frame.
 *
 * Two-tier hit: a lookup can return a partial hit (func_id valid, line == -1) when the
 * function is cached but lasti changed. The caller re-parses the line table for the current
 * lasti without re-interning the function — skipping two of the three expensive libdd calls.
 *
 * Concurrency: heap-profiler hooks run under the GIL; no internal locking needed.
 *
 * Lifetime: cleared on postfork_child and profiler stop/restart. libdd function_ids do not
 * expire while their owning ProfilesDictionary lives, which spans the whole profiler
 * lifetime (verified in _memalloc_heap.cpp -- allocs_m holds samples whose locations
 * reference function_ids and only clears at postfork_child).
 */
class CodeFunctionCache
{
  public:
    static constexpr size_t DEFAULT_CAPACITY = 1024;
    static constexpr size_t MIN_CAPACITY = 64;
    static constexpr size_t MAX_CAPACITY = 1 << 20; // 1M cap as a sanity ceiling

    explicit CodeFunctionCache(size_t capacity);

    /* Returns a CacheHit for code only if present AND its stored identity still matches
     * the supplied (name, filename, firstlineno), guarding against PyCodeObject address
     * reuse. Check hit.func_id != nullptr for a hit; hit.line >= 0 means lasti matched
     * the stored value so the caller can skip parse_linetable. */
    CacheHit lookup(PyCodeObject* code, PyObject* name, PyObject* filename, int firstlineno, int lasti) noexcept;

    /* Inserts (code, id) with the identity used to validate future lookups plus the
     * (lasti, line) pair. Evicts one entry if the map is at capacity. */
    void insert(PyCodeObject* code,
                Datadog::function_id id,
                PyObject* name,
                PyObject* filename,
                int firstlineno,
                int lasti,
                int line);

    /* Drops every entry, retaining reserved capacity. */
    void clear();

    /* Process-wide singleton, mirrors heap_tracker_t::instance. */
    static CodeFunctionCache* instance;

  private:
    struct Entry
    {
        Datadog::function_id func_id;
        PyObject* name;
        PyObject* filename;
        int firstlineno;
        int lasti;
        int line;
    };

    CodeCacheMap<PyCodeObject*, Entry> map_;
    size_t max_capacity_;
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
