#include "_memalloc_code_cache.h"

#include <algorithm>
#include <memory>

namespace Datadog {

/* g_instance_owned holds the singleton so the object is cleaned up at exit
 * even if memalloc_code_cache_deinit() is never called. CodeFunctionCache::instance
 * mirrors g_instance_owned.get() and is the pointer read on every hot-path frame walk;
 * keeping it raw avoids an extra indirection through the unique_ptr on the allocator hook. */
static std::unique_ptr<CodeFunctionCache> g_instance_owned;
CodeFunctionCache* CodeFunctionCache::instance = nullptr;

CodeFunctionCache::CodeFunctionCache(size_t capacity)
  : max_capacity_(std::clamp(capacity, MIN_CAPACITY, MAX_CAPACITY))
{
    /* Reserve up-front so the map performs no heap allocations on insert until
     * max_capacity_ entries are present. absl::flat_hash_map::reserve(n) ensures
     * capacity for at least n elements before the next rehash. */
    map_.reserve(max_capacity_);
}

CacheResult
CodeFunctionCache::lookup(PyCodeObject* code, PyObject* name, PyObject* filename, int firstlineno) noexcept
{
    auto it = map_.find(code);
    if (it == map_.end()) {
        return { nullptr };
    }
    const Entry& e = it->second;
    /* Validate identity to defend against PyCodeObject address reuse:
     * CPython may free a code object and hand its address to a new one.
     * On mismatch the entry is stale; report a miss so the caller re-interns. */
    if (e.name != name || e.filename != filename || e.firstlineno != firstlineno) {
        return { nullptr };
    }
    return { e.func_id };
}

void
CodeFunctionCache::insert(PyCodeObject* code,
                          Datadog::function_id id,
                          PyObject* name,
                          PyObject* filename,
                          int firstlineno)
{
    /* Evict one entry when at capacity to keep the map bounded and prevent
     * a rehash. Eviction policy is effectively random (first occupied slot);
     * the policy rarely fires in practice — typical workloads have far fewer
     * than max_capacity_ unique frames on the hot path. */
    if (map_.size() >= max_capacity_) {
        map_.erase(map_.begin());
    }
    map_.insert_or_assign(code, Entry{ id, name, filename, firstlineno });
}

void
CodeFunctionCache::clear()
{
    /* clear() retains reserved capacity, so postfork_child resets entries
     * without releasing the pre-allocated flat storage. */
    map_.clear();
}

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
