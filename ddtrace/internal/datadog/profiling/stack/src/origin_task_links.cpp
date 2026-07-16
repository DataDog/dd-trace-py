#include "origin_task_links.hpp"

#include <cstdint>
#include <mutex>
#include <optional>
#include <string>

namespace Datadog {
void
OriginTaskLinks::link_origin_task(uint64_t thread_id, uint64_t task_id, std::string task_name)
{
    std::lock_guard<std::mutex> lock(mtx);

    auto [it, inserted] = thread_id_to_origin_task.try_emplace(thread_id, task_id, std::move(task_name));
    if (!inserted) {
        it->second.task_id = task_id;
        it->second.task_name = std::move(task_name);
    }
}

const std::optional<OriginTask>
OriginTaskLinks::get_origin_task(uint64_t thread_id)
{
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
    std::lock_guard<std::mutex> lock(mtx);

    thread_id_to_origin_task.erase(thread_id); // This is a no-op if the key is not found
}

void
OriginTaskLinks::reset()
{
    std::lock_guard<std::mutex> lock(mtx);
    thread_id_to_origin_task.clear();
}

void
OriginTaskLinks::postfork_child()
{
    auto& instance = get_instance();
    new (&instance.mtx) std::mutex();
    new (&instance.thread_id_to_origin_task) std::unordered_map<uint64_t, OriginTask>();
}

} // namespace Datadog
