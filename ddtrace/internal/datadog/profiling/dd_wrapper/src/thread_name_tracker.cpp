#include "thread_name_tracker.hpp"

#include <mutex>
#include <shared_mutex>
#include <string>
#include <string_view>

namespace Datadog {

void
ThreadNameRegistry::register_name(int64_t thread_id, std::string_view name)
{
    std::unique_lock lock(mtx);
    auto it = thread_names.find(thread_id);
    if (it == thread_names.end()) {
        if (thread_names.size() >= max_thread_names) {
            return;
        }
        thread_names.emplace(thread_id, std::string(name));
    } else {
        it->second.assign(name);
    }
}

void
ThreadNameRegistry::unregister_name(int64_t thread_id)
{
    std::unique_lock lock(mtx);
    thread_names.erase(thread_id);
}

std::string_view
ThreadNameRegistry::lookup(int64_t thread_id)
{
    // Copying the name out keeps the view valid after the lock is dropped:
    // rehashing on a later register_name would otherwise invalidate it.
    static thread_local std::string name;

    std::shared_lock lock(mtx, std::try_to_lock);
    if (!lock.owns_lock()) {
        return {};
    }

    auto it = thread_names.find(thread_id);
    if (it == thread_names.end()) {
        return {};
    }

    name = it->second;
    return name;
}

void
ThreadNameRegistry::reset()
{
    std::unique_lock lock(mtx);
    thread_names.clear();
}

void
ThreadNameRegistry::postfork_child()
{
    // NB placement-new to re-init the mutex because doing anything else is UB.
    // We intentionally do NOT clear thread_names. Only the forking thread
    // survives in the child, and it keeps its thread id, so dropping the map
    // would lose that thread's name with nothing to re-populate it until the
    // profiler restarts. Entries for threads that did not survive are inert:
    // lookups are keyed by thread id, and those ids are no longer running.
    new (&mtx) std::shared_mutex();
}

size_t
ThreadNameRegistry::size() const
{
    std::shared_lock lock(mtx);
    return thread_names.size();
}

} // namespace Datadog
