#pragma once

#include <mutex>
#include <optional>
#include <stdint.h>
#include <string>
#include <unordered_map>

namespace Datadog {

// Identity of the asyncio task that submitted work to a ThreadPoolExecutor.
// task_id is the task object's address (id(task) in Python)
struct OriginTask
{
    uint64_t task_id;
    std::string task_name;

    OriginTask(uint64_t _task_id, std::string _task_name)
      : task_id(_task_id)
      , task_name(std::move(_task_name))
    {
    }

    // for testing
    bool operator==(const OriginTask& other) const { return task_id == other.task_id && task_name == other.task_name; }
};

// Per-(worker)-thread registry of the asyncio task that offloaded the work
// currently running on that thread. Populated by the futures integration for
// the duration of an executor job and accessed by stack renderer
class OriginTaskLinks
{
  public:
    static OriginTaskLinks& get_instance()
    {
        static OriginTaskLinks instance;
        return instance;
    }

    OriginTaskLinks(OriginTaskLinks const&) = delete;
    OriginTaskLinks& operator=(OriginTaskLinks const&) = delete;

    void link_origin_task(uint64_t thread_id, uint64_t task_id, std::string task_name);
    const std::optional<OriginTask> get_origin_task(uint64_t thread_id);
    void unlink_origin_task(uint64_t thread_id);

    static void postfork_child();

  private:
    std::mutex mtx;
    std::unordered_map<uint64_t, OriginTask> thread_id_to_origin_task;

    // Private Constructor/Destructor
    OriginTaskLinks() = default;
    ~OriginTaskLinks() = default;
};

}
