#include "_memalloc_code_cache.h"

#include <algorithm>
#include <bit>
#include <memory>

namespace Datadog {

/* g_instance_owned holds the singleton so the object is cleaned up at exit
 * even if memalloc_code_cache_deinit() is never called. CodeFunctionCache::instance
 * mirrors g_instance_owned.get() and is the pointer read on every hot-path frame walk;
 * keeping it raw avoids an extra indirection through the unique_ptr on the allocator hook. */
static std::unique_ptr<CodeFunctionCache> g_instance_owned;
CodeFunctionCache* CodeFunctionCache::instance = nullptr;

static size_t
clamp_to_pow2_set_count(size_t capacity_hint)
{
    /* Capacity is total ways. num_sets = capacity / WAYS_PER_SET, rounded up
     * to a power of two so set indexing can use a bitmask. */
    size_t clamped = std::max(capacity_hint, CodeFunctionCache::MIN_CAPACITY);
    clamped = std::min(clamped, CodeFunctionCache::MAX_CAPACITY);

    size_t target_sets = (clamped + CodeFunctionCache::WAYS_PER_SET - 1) / CodeFunctionCache::WAYS_PER_SET;
    size_t num_sets = 1;
    while (num_sets < target_sets) {
        num_sets <<= 1;
    }
    return num_sets;
}

CodeFunctionCache::CodeFunctionCache(size_t capacity_hint)
{
    size_t num_sets = clamp_to_pow2_set_count(capacity_hint);
    sets_.assign(num_sets, Set{});
    /* num_sets is a power of two, so countr_zero gives log2(num_sets).
     * std::countr_zero (<bit>, C++20) is portable across GCC/Clang/MSVC. */
    log2_set_bits_ = static_cast<uint8_t>(std::countr_zero(num_sets));
}

size_t
CodeFunctionCache::set_index(PyCodeObject* code) const
{
    /* Fibonacci hashing: multiply by 2^64 / phi (Knuth TAOCP 6.4) and take
     * the high log2(num_sets) bits. One imul + one shift; uniform on the
     * clustered low bits of pymalloc-allocated PyCodeObject pointers that
     * the previous shift-mask scheme piled into a few sets. */
    constexpr uint64_t FIB_MUL = 0x9E3779B97F4A7C15ULL;
    uint64_t bits = static_cast<uint64_t>(reinterpret_cast<uintptr_t>(code));
    return static_cast<size_t>((bits * FIB_MUL) >> (64 - log2_set_bits_));
}

CacheHit
CodeFunctionCache::lookup(PyCodeObject* code, PyObject* name, PyObject* filename, int firstlineno, int lasti) noexcept
{
    Set& s = sets_[set_index(code)];
    for (size_t i = 0; i < WAYS_PER_SET; ++i) {
        if (s.codes[i] == code) {
            /* Validate identity to defend against PyCodeObject address reuse:
             * CPython may free a code object and hand its address to a new one.
             * On mismatch the slot is stale; report a miss so the caller
             * re-interns and overwrites it via insert(). */
            if (s.names[i] == name && s.filenames[i] == filename && s.firstlines[i] == firstlineno) {
                /* line >= 0 only when the stored lasti matches, letting the
                 * caller skip parse_linetable; otherwise -1 signals a reparse. */
                return CacheHit{ s.functions[i], s.lastis[i] == lasti ? s.lines[i] : -1 };
            }
            return CacheHit{ nullptr, -1 };
        }
    }
    return CacheHit{ nullptr, -1 };
}

void
CodeFunctionCache::insert(PyCodeObject* code,
                          Datadog::function_id id,
                          PyObject* name,
                          PyObject* filename,
                          int firstlineno,
                          int lasti,
                          int line)
{
    Set& s = sets_[set_index(code)];

    for (size_t i = 0; i < WAYS_PER_SET; ++i) {
        if (s.codes[i] == nullptr || s.codes[i] == code) {
            s.codes[i] = code;
            s.functions[i] = id;
            s.names[i] = name;
            s.filenames[i] = filename;
            s.firstlines[i] = firstlineno;
            s.lastis[i] = lasti;
            s.lines[i] = line;
            return;
        }
    }

    /* FIFO eviction: overwrite next_evict slot, advance pointer. */
    size_t way = s.next_evict;
    s.next_evict = static_cast<uint8_t>((way + 1) % WAYS_PER_SET);
    s.codes[way] = code;
    s.functions[way] = id;
    s.names[way] = name;
    s.filenames[way] = filename;
    s.firstlines[way] = firstlineno;
    s.lastis[way] = lasti;
    s.lines[way] = line;
}

void
CodeFunctionCache::clear()
{
    std::fill(sets_.begin(), sets_.end(), Set{});
}

/* Note: we do not use std::unordered_map here because STL containers call the
 * default allocator internally. Since our hooks intercept PyObject_Malloc and
 * PyMem_RawMalloc, any allocation inside a hook would immediately re-enter it,
 * causing infinite recursion. The custom fixed-capacity array is bounded and
 * performs no heap allocations after construction. */
bool
memalloc_code_cache_init(size_t capacity)
{
    memalloc_code_cache_deinit();
    g_instance_owned = std::make_unique<CodeFunctionCache>(capacity);
    CodeFunctionCache::instance = g_instance_owned.get();
    return true;
}

void
memalloc_code_cache_deinit()
{
    CodeFunctionCache::instance = nullptr;
    g_instance_owned.reset();
}

/* Called from heap_tracker_t::postfork_child() to reset the cache after fork.
 * The parent's cached function_ids remain valid in the child (libdatadog's
 * ProfilesDictionary is not forked), but the child gets a fresh profiler
 * session, so we clear to avoid mixing function_ids from two sessions. */
void
memalloc_code_cache_clear()
{
    if (CodeFunctionCache::instance != nullptr) {
        CodeFunctionCache::instance->clear();
    }
}

} // namespace Datadog
