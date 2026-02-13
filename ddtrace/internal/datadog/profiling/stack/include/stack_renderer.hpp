#pragma once

#include <cstdint>
#include <string>
#include <string_view>

#include "python_headers.hpp"

#include "dd_wrapper/include/sample.hpp"

#include "echion/frame.h"
#include "echion/timing.h"

namespace Datadog {

enum class MetricType : std::uint8_t
{
    Time,
    Memory
};

struct ThreadState
{
    // Current thread info.  Keeping one instance of this per StackRenderer is sufficient because the renderer visits
    // threads one at a time.
    // The only time this information is revealed is when the sampler observes a thread. When the sampler goes on to
    // process tasks, it needs to place thread-level information in the Sample.
    uintptr_t id = 0;
    unsigned long native_id = 0;
    std::string name;
    microsecond_t wall_time_ns = 0;
    microsecond_t cpu_time_ns = 0;
    int64_t now_time_ns = 0;
};

class StackRenderer
{
    Sample* sample = nullptr;
    ThreadState thread_state = {};
    // Whether task name has been pushed for the current sample. Whenever
    // the sample is created, this has to be reset.
    bool pushed_task_name = false;

  public:
    void render_thread_begin(PyThreadState* tstate,
                             std::string_view name,
                             microsecond_t wall_time_us,
                             uintptr_t thread_id,
                             unsigned long native_id);
    void render_task_begin(std::string task_name, bool on_cpu);
    void render_stack_begin();
    void render_frame(Frame& frame);
    void render_cpu_time(uint64_t cpu_time_us);
    void render_stack_end();
};

} // namespace Datadog
