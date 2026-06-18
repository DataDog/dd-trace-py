#include "_memalloc_code_cache.h"

#include <algorithm>
#include <bit>
#include <cstdlib>
#include <cstring>

namespace Datadog {

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

std::array<size_t, CodeFunctionCache::WAYS_PER_SET + 1>
CodeFunctionCache::occupancy_histogram() const
{
    std::array<size_t, WAYS_PER_SET + 1> hist{};
    for (const Set& s : sets_) {
        size_t occupied = 0;
        for (size_t i = 0; i < WAYS_PER_SET; ++i) {
            if (s.codes[i] != nullptr) {
                ++occupied;
            }
        }
        ++hist[occupied];
    }
    return hist;
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
                ++hits_;
                /* line >= 0 only when the stored lasti matches, letting the
                 * caller skip parse_linetable; otherwise -1 signals a reparse. */
                return CacheHit{ s.functions[i], s.lastis[i] == lasti ? s.lines[i] : -1 };
            }
            ++misses_;
            return CacheHit{ nullptr, -1 };
        }
    }
    ++misses_;
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
    ++evictions_;
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

void
CodeFunctionCache::reset_counters()
{
    hits_ = 0;
    misses_ = 0;
    evictions_ = 0;
}

bool
memalloc_code_cache_init(size_t capacity)
{
    if (CodeFunctionCache::instance != nullptr) {
        return false;
    }
    CodeFunctionCache::instance = new CodeFunctionCache(capacity);
    return true;
}

void
memalloc_code_cache_deinit()
{
    CodeFunctionCache* old = CodeFunctionCache::instance;
    CodeFunctionCache::instance = nullptr;
    delete old;
}

void
memalloc_code_cache_clear()
{
    if (CodeFunctionCache::instance != nullptr) {
        CodeFunctionCache::instance->clear();
    }
}

} // namespace Datadog
