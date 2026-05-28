#include "_memalloc_code_cache.h"

#include <algorithm>
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
    log2_set_bits_ = static_cast<uint8_t>(__builtin_ctzll(num_sets));
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

std::optional<Datadog::function_id>
CodeFunctionCache::lookup(PyCodeObject* code)
{
    Set& s = sets_[set_index(code)];
    for (size_t i = 0; i < WAYS_PER_SET; ++i) {
        if (s.codes[i] == code) {
            s.recently_used_mask = set_way_used(s.recently_used_mask, i);
            ++hits_;
            return s.functions[i];
        }
    }
    ++misses_;
    return std::nullopt;
}

void
CodeFunctionCache::insert(PyCodeObject* code, Datadog::function_id id)
{
    Set& s = sets_[set_index(code)];

    /* Pass 1: empty or duplicate slot. */
    for (size_t i = 0; i < WAYS_PER_SET; ++i) {
        if (s.codes[i] == nullptr) {
            s.codes[i] = code;
            s.functions[i] = id;
            s.recently_used_mask = set_way_used(s.recently_used_mask, i);
            return;
        }
        if (s.codes[i] == code) {
            s.functions[i] = id;
            s.recently_used_mask = set_way_used(s.recently_used_mask, i);
            return;
        }
    }

    /* Pass 2: CLOCK / Second-Chance. Sweep ways starting from clock_hand;
     * a way with recently_used=0 is evicted. Ways with recently_used=1 get
     * cleared and the hand advances. Termination is guaranteed within
     * 2 * WAYS_PER_SET iterations because each visited way that survives
     * the first pass has its bit cleared. */
    for (size_t step = 0; step < 2 * WAYS_PER_SET; ++step) {
        size_t way = s.clock_hand;
        s.clock_hand = static_cast<uint8_t>((s.clock_hand + 1) % WAYS_PER_SET);
        if (!way_recently_used(s.recently_used_mask, way)) {
            ++evictions_;
            s.codes[way] = code;
            s.functions[way] = id;
            s.recently_used_mask = set_way_used(s.recently_used_mask, way);
            return;
        }
        s.recently_used_mask = clear_way_used(s.recently_used_mask, way);
    }
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

static size_t
read_capacity_from_env()
{
    const char* raw = std::getenv("DD_PROFILING_MEMALLOC_CODE_CACHE_SIZE");
    if (raw == nullptr || raw[0] == '\0') {
        return CodeFunctionCache::DEFAULT_CAPACITY;
    }
    char* endptr = nullptr;
    long long parsed = std::strtoll(raw, &endptr, 10);
    if (endptr == raw || parsed <= 0) {
        return CodeFunctionCache::DEFAULT_CAPACITY;
    }
    return static_cast<size_t>(parsed);
}

bool
memalloc_code_cache_init()
{
    if (CodeFunctionCache::instance != nullptr) {
        return false;
    }
    CodeFunctionCache::instance = new CodeFunctionCache(read_capacity_from_env());
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
