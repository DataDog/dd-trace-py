#include "_memalloc_code_cache.h"

#include <algorithm>
#include <memory>

namespace Datadog {

/* g_instance_owned holds the memalloc cache singleton so the object is cleaned up at exit
 * even if memalloc_code_cache_deinit is never called. CodeFunctionCache::instance
 * mirrors g_instance_owned.get() and is set to nullptr first in deinit(), so any
 * in-flight frame walk under the GIL sees nullptr and skips the cache cleanly before
 * the object is destroyed. */
static std::unique_ptr<CodeFunctionCache> g_instance_owned;
CodeFunctionCache* CodeFunctionCache::instance = nullptr;

CodeFunctionCache::CodeFunctionCache(size_t capacity)
  : max_capacity_(std::clamp(capacity, MIN_CAPACITY, MAX_CAPACITY))
{
    map_.reserve(max_capacity_);
}

Datadog::function_id
CodeFunctionCache::lookup(PyCodeObject* code, PyObject* name, PyObject* filename, int firstlineno) noexcept
{
    auto it = map_.find(code);
    if (it == map_.end()) {
        return nullptr;
    }
    const Entry& e = it->second;
    /* Validate identity to defend against PyCodeObject address reuse:
     * CPython may free a code object and hand its address to a new one.
     * On mismatch the entry is stale; report a miss so the caller re-interns. */
    if (e.name != name || e.filename != filename || e.firstlineno != firstlineno) {
        return nullptr;
    }
    return e.func_id;
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

} // namespace Datadog
