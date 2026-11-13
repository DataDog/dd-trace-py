#include "thread_name_tracker.hpp"

#include <algorithm>
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

void
ThreadNameRegistry::replace_all(std::unordered_map<int64_t, std::string_view> const& names)
{
    // Build and free the maps outside the lock: their allocations can be
    // sampled, and lookup gives up while the write lock is held.
    std::unordered_map<int64_t, std::string> new_names;
    new_names.reserve(std::min(names.size(), max_thread_names));
    for (auto const& [thread_id, name] : names) {
        if (new_names.size() >= max_thread_names) {
            break;
        }
        new_names.emplace(thread_id, std::string(name));
    }

    {
        std::unique_lock lock(mtx);
        thread_names.swap(new_names);
    }
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
ThreadNameRegistry::prefork()
{
    mtx.lock();
}

void
ThreadNameRegistry::postfork_parent()
{
    mtx.unlock();
}

void
ThreadNameRegistry::postfork_child()
{
    // prefork holds the write lock, so the map is consistent.
    // Re-create the mutex instead of unlocking it: the inherited lock still records waiters
    // that do not exist in the child, and glibc hands a released write lock
    // directly to a waiting writer, which would leave it held forever.
    new (&mtx) std::shared_mutex();
}

size_t
ThreadNameRegistry::size() const
{
    std::shared_lock lock(mtx);
    return thread_names.size();
}

} // namespace Datadog
