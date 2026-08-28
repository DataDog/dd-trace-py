#include "origin_task_links.hpp"

#include <cstdint>
#include <mutex>
#include <optional>
#include <string>

namespace Datadog {

void
OriginTaskLinks::enable()
{
    enabled_.store(true, std::memory_order_release);
}

void
OriginTaskLinks::disable_and_reset()
{
    // Clear the flag before taking the map mutex so a worker that observed
    // enabled before stop cannot insert a stale entry after the map is cleared.
    enabled_.store(false, std::memory_order_release);
    std::lock_guard<std::mutex> lock(mtx);
    thread_id_to_origin_task.clear();
}

void
OriginTaskLinks::link_origin_task(uint64_t thread_id, uint64_t task_id, std::string task_name)
{
    if (!is_enabled()) {
        return;
    }

    std::lock_guard<std::mutex> lock(mtx);
    // Re-check under the mutex: disable_and_reset() may have cleared the map
    // between the first check and lock acquisition.
    if (!is_enabled()) {
        return;
    }

    auto [it, inserted] = thread_id_to_origin_task.try_emplace(thread_id, task_id, std::move(task_name));
    if (!inserted) {
        it->second.task_id = task_id;
        it->second.task_name = std::move(task_name);
    }
}

const std::optional<OriginTask>
OriginTaskLinks::get_origin_task(uint64_t thread_id)
{
    if (!is_enabled()) {
        return std::nullopt;
    }

    std::lock_guard<std::mutex> lock(mtx);

    auto it = thread_id_to_origin_task.find(thread_id);
    if (it != thread_id_to_origin_task.end()) {
        return it->second;
    }
    return std::nullopt;
}

void
OriginTaskLinks::unlink_origin_task(uint64_t thread_id)
{
    if (!is_enabled()) {
        return;
    }

    std::lock_guard<std::mutex> lock(mtx);

    thread_id_to_origin_task.erase(thread_id); // This is a no-op if the key is not found
}

void
OriginTaskLinks::postfork_child()
{
    auto& instance = get_instance();
    // Inherited enabled_ may still be true from the parent; stay disabled until
    // the child sampler is restarted and enable() is called again.
    instance.enabled_.store(false, std::memory_order_relaxed);
    // NB placement-new to re-init and leak the mutex because doing anything else is UB
    new (&instance.mtx) std::mutex();
    // Map may be mid-mutation if fork raced with link/unlink; reconstruct
    // without inspecting contents.
    new (&instance.thread_id_to_origin_task) std::unordered_map<uint64_t, OriginTask>();
}

} // namespace Datadog
