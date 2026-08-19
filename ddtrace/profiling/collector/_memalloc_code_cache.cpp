#include "_memalloc_code_cache.h"

#include "_pymacro.h"

#include <algorithm>
#include <atomic>
#include <bit>
#include <mutex>

#if defined(_PY312_AND_LATER)
#include <cpython/code.h>
#endif

namespace Datadog {

#if defined(_PY312_AND_LATER)
static std::atomic<uint64_t> g_code_object_destroy_generation{ 0 };

static int
code_object_destroy_watcher(PyCodeEvent event, PyCodeObject* /*co*/)
{
    if (event == PY_CODE_EVENT_DESTROY) {
        g_code_object_destroy_generation.fetch_add(1, std::memory_order_relaxed);
    }
    return 0;
}

static bool
register_code_object_watcher()
{
    static std::once_flag once;
    static bool registered = false;
    std::call_once(once, []() {
        registered = PyCode_AddWatcher(code_object_destroy_watcher) >= 0;
    });
    return registered;
}

static uint64_t
current_code_object_generation()
{
    return g_code_object_destroy_generation.load(std::memory_order_relaxed);
}
#endif // _PY312_AND_LATER

/* CodeFunctionCache::instance is set in memalloc_code_cache_init() and cleared before
 * delete in memalloc_code_cache_deinit(). Avoid static destructors so the cache is not
 * torn down during native static teardown while allocator hooks may still be installed
 * when memalloc.stop() was skipped. */
CodeFunctionCache* CodeFunctionCache::instance = nullptr;

static size_t
clamp_to_pow2_set_count(size_t capacity_hint)
{
    size_t clamped = std::clamp(capacity_hint, CodeFunctionCache::MIN_CAPACITY, CodeFunctionCache::MAX_CAPACITY);
    size_t num_sets = clamped / CodeFunctionCache::WAYS_PER_SET;
    if (num_sets < 1) {
        num_sets = 1;
    }
    return std::bit_floor(num_sets);
}

CodeFunctionCache::CodeFunctionCache(size_t capacity_hint)
{
    size_t num_sets = clamp_to_pow2_set_count(capacity_hint);
    sets_.assign(num_sets, Set{});
    log2_set_bits_ = static_cast<uint8_t>(std::countr_zero(num_sets));
#if defined(_PY312_AND_LATER)
    validated_generation_ = current_code_object_generation();
#endif
}

size_t
CodeFunctionCache::set_index(PyCodeObject* code) const
{
    /* Fibonacci (multiplicative) hashing. FIB_MUL is 2^64 / phi (the golden ratio),
     * so multiplying the pointer scatters its bits across the whole 64-bit word; the
     * top log2_set_bits_ bits then select the set. pymalloc hands out PyCodeObject*
     * addresses that share low-bit structure (fixed size-class strides), which a plain
     * `ptr % num_sets` would map onto only a few sets. The golden-ratio multiply spreads
     * those clustered pointers evenly instead. See Knuth TAOCP vol. 3, 6.4. */
    constexpr uint64_t FIB_MUL = 0x9E3779B97F4A7C15ULL;
    uint64_t bits = static_cast<uint64_t>(reinterpret_cast<uintptr_t>(code));
    return static_cast<size_t>((bits * FIB_MUL) >> (64 - log2_set_bits_));
}

Datadog::function_id
CodeFunctionCache::lookup(PyCodeObject* code, PyObject* name, PyObject* filename, int firstlineno) noexcept
{
    Set& s = sets_[set_index(code)];
    for (size_t i = 0; i < WAYS_PER_SET; ++i) {
        if (s.codes[i] != code) {
            continue;
        }
        /* Validate identity to defend against PyCodeObject address reuse:
         * CPython may free a code object and hand its address to a new one.
         * On mismatch the entry is stale; report a miss so the caller re-interns. */
        if (s.names[i] != name || s.filenames[i] != filename || s.firstlines[i] != firstlineno) {
            return nullptr;
        }
        return s.functions[i];
    }
    return nullptr;
}

void
CodeFunctionCache::insert(PyCodeObject* code,
                          Datadog::function_id id,
                          PyObject* name,
                          PyObject* filename,
                          int firstlineno)
{
    Set& s = sets_[set_index(code)];
    for (size_t i = 0; i < WAYS_PER_SET; ++i) {
        if (s.codes[i] == nullptr || s.codes[i] == code) {
            s.codes[i] = code;
            s.functions[i] = id;
            s.names[i] = name;
            s.filenames[i] = filename;
            s.firstlines[i] = firstlineno;
            return;
        }
    }
    size_t way = s.next_evict;
    s.next_evict = static_cast<uint8_t>((way + 1) % WAYS_PER_SET);
    s.codes[way] = code;
    s.functions[way] = id;
    s.names[way] = name;
    s.filenames[way] = filename;
    s.firstlines[way] = firstlineno;
}

void
CodeFunctionCache::clear()
{
    std::fill(sets_.begin(), sets_.end(), Set{});
#if defined(_PY312_AND_LATER)
    validated_generation_ = current_code_object_generation();
#endif
}

void
CodeFunctionCache::prepare_stack_walk()
{
#if defined(_PY312_AND_LATER)
    uint64_t generation = current_code_object_generation();
    if (generation != validated_generation_) {
        clear();
    }
#endif
}

bool
memalloc_code_cache_init(size_t capacity)
{
#if !defined(_PY312_AND_LATER)
    (void)capacity;
    return true;
#else
    if (!register_code_object_watcher()) {
        return true;
    }

    memalloc_code_cache_deinit();
    CodeFunctionCache* cache = new CodeFunctionCache(capacity);
    CodeFunctionCache::instance = cache;
    return true;
#endif
}

void
memalloc_code_cache_deinit()
{
    CodeFunctionCache* cache = CodeFunctionCache::instance;
    CodeFunctionCache::instance = nullptr;
    delete cache;
}

/* Called from heap_tracker_t::postfork_child() to reset the cache after fork.
 * libdatadog's ProfilesDictionary is dropped and recreated in the child
 * (profiler_state.cpp::postfork_child_state()), so the parent's cached
 * function_ids are no longer valid. Clear all entries so subsequent lookups
 * re-intern against the new ProfilesDictionary rather than producing
 * misattributed frames. */
void
memalloc_code_cache_clear()
{
    if (CodeFunctionCache::instance != nullptr) {
        CodeFunctionCache::instance->clear();
    }
}

void
memalloc_code_cache_prepare_stack_walk()
{
    if (CodeFunctionCache::instance != nullptr) {
        CodeFunctionCache::instance->prepare_stack_walk();
    }
}

} // namespace Datadog
