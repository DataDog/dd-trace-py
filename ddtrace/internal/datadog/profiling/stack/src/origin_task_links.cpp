#include "origin_task_links.hpp"

#include <mutex>
#include <optional>
#include <stdint.h>
#include <string>

namespace Datadog {
void
OriginTaskLinks::link_origin_task(uint64_t thread_id, uint64_t task_id, std::string task_name)
{
    std::lock_guard<std::mutex> lock(mtx);

    auto it = thread_id_to_origin_task.find(thread_id);
    if (it == thread_id_to_origin_task.end()) {
        thread_id_to_origin_task[thread_id] = std::make_unique<OriginTask>(task_id, std::move(task_name));
    } else {
        it->second->task_id = task_id;
        it->second->task_name = std::move(task_name);
    }
}

const std::optional<OriginTask>
OriginTaskLinks::get_origin_task_from_thread_id(uint64_t thread_id)
{
    std::lock_guard<std::mutex> lock(mtx);

    std::optional<OriginTask> origin_task;
    auto it = thread_id_to_origin_task.find(thread_id);
    if (it != thread_id_to_origin_task.end()) {
        origin_task = *(it->second);
    }
    return origin_task;
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
    // NB placement-new to re-init and leak the mutex because doing anything else is UB
    new (&get_instance().mtx) std::mutex();
    get_instance().reset();
}

} // namespace Datadog
