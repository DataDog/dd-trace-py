#include "current_task_links.hpp"

#include <algorithm>
#include <cstring>

namespace {

// Per-OS-thread current-task record. Backed entirely by thread-local scalars
// and a fixed-size char buffer so that recording a task never allocates and can
// therefore never re-enter the memory allocator hook.
//
// We explicitly request the global-dynamic TLS access model because this code
// lives in a library that is dlopened by the host interpreter; global-dynamic
// is the most general (and always-correct) model for that scenario.
#define CURRENT_TASK_TLS __attribute__((tls_model("global-dynamic"))) thread_local

CURRENT_TASK_TLS bool t_has_task = false;
CURRENT_TASK_TLS int64_t t_task_id = 0;
CURRENT_TASK_TLS size_t t_task_name_len = 0;
CURRENT_TASK_TLS char t_task_name[Datadog::CurrentTaskLinks::k_max_task_name];

} // namespace

void
Datadog::CurrentTaskLinks::set_current_task(int64_t task_id, std::string_view task_name)
{
    t_task_id = task_id;

    const size_t copy_len = std::min(task_name.size(), k_max_task_name);
    if (copy_len > 0) {
        std::memcpy(t_task_name, task_name.data(), copy_len);
    }
    t_task_name_len = copy_len;

    // Publish last so a same-thread reader never observes a stale name with a
    // fresh id (writes here are not interruptible by allocation anyway).
    t_has_task = true;
}

void
Datadog::CurrentTaskLinks::clear_current_task()
{
    t_has_task = false;
    t_task_name_len = 0;
}

bool
Datadog::CurrentTaskLinks::get_current_task(int64_t* task_id, std::string_view* task_name)
{
    if (!t_has_task) {
        return false;
    }

    *task_id = t_task_id;
    *task_name = std::string_view(t_task_name, t_task_name_len);
    return true;
}

void
Datadog::CurrentTaskLinks::postfork_child()
{
    clear_current_task();
}
