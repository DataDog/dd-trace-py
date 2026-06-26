#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "sample.hpp"

#include <cstddef>
#include <cstdint>

/* Use absl::flat_hash_map in production builds (open-addressing, contiguous
 * storage, no per-insert heap allocation once reserved). Fall back to
 * std::unordered_map in debug builds where Abseil may not be compiled in. */
#if defined(NDEBUG) && !defined(DONT_COMPILE_ABSEIL)
#include "absl/container/flat_hash_map.h"

/* Fibonacci (golden-ratio) hash for PyCodeObject pointers.
 *
 * pymalloc allocates PyCodeObject from fixed-size arenas, so pointers cluster
 * on 8-byte-aligned addresses with many low bits fixed. A naive modulo/shift
 * hash piles those into the same buckets. Multiplying by 2^64/phi (Knuth
 * TAOCP 6.4) disperses the clustered low bits across the full 64-bit range.
 *
 * absl uses the high bits of the hash as the slot index and the low 7 bits as
 * a per-slot fingerprint. Fibonacci output is well-distributed in both halves,
 * so collision probability stays close to the theoretical minimum regardless
 * of pointer alignment patterns. */
struct FibHashPyCode
{
    size_t operator()(PyCodeObject* p) const noexcept
    {
        constexpr uint64_t FIB_MUL = 0x9E3779B97F4A7C15ULL;
        return static_cast<size_t>(static_cast<uint64_t>(reinterpret_cast<uintptr_t>(p)) * FIB_MUL);
    }
};

template<typename K, typename V>
using CodeCacheMap = absl::flat_hash_map<K, V, FibHashPyCode>;
#else
#include <unordered_map>
template<typename K, typename V>
using CodeCacheMap = std::unordered_map<K, V>;
#endif

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
 * Implementation: absl::flat_hash_map reserved at construction to max_capacity entries.
 * Reservation avoids all post-construction heap allocations for the common case (typical
 * workloads stay well under 1024 unique frames on the hot path). When capacity is exceeded,
 * one entry is evicted before inserting the new one, keeping map size bounded.
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
