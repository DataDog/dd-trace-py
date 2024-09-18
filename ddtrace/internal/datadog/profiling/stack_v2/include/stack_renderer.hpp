#pragma once

#include <chrono>
#include <fstream>
#include <memory>
#include <mutex>
#include <string>
#include <string_view>
#include <thread>
#include <vector>

#include "python_headers.hpp"

#include "dd_wrapper/include/ddup_interface.hpp"
#include "echion/render.h"

namespace Datadog {

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

class StackRenderer : public RendererInterface
{
    Sample* sample = nullptr;
    ThreadState thread_state = {};

    virtual void render_message(std::string_view msg) override;
    virtual void render_thread_begin(PyThreadState* tstate,
                                     std::string_view name,
                                     microsecond_t wall_time_us,
                                     uintptr_t thread_id,
                                     unsigned long native_id) override;
    virtual void render_task_begin(std::string_view name);
    virtual void render_stack_begin() override;
    virtual void render_python_frame(std::string_view name, std::string_view file, uint64_t line) override;
    virtual void render_native_frame(std::string_view name, std::string_view file, uint64_t line) override;
    virtual void render_cpu_time(microsecond_t cpu_time_us) override;
    virtual void render_stack_end() override;
    virtual bool is_valid() override;
};

} // namespace Datadog
