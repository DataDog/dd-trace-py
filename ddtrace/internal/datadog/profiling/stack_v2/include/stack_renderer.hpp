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

#include "dd_wrapper/include/interface.hpp"
#include "echion/render.h"

namespace Datadog {

class StackRenderer : public RendererInterface
{
    Sample* sample = nullptr;

    // Stashed thread information
    uintptr_t stashed_thread_id = 0;
    unsigned long stashed_native_id = 0;
    std::string stashed_thread_name;
    microsecond_t stashed_wall_time_us = 0;
    microsecond_t stashed_cpu_time_us = 0;

    virtual void render_message(std::string_view msg) override;
    virtual void render_thread_begin(PyThreadState* tstate,
                                     std::string_view name,
                                     microsecond_t wall_time_us,
                                     uintptr_t thread_id,
                                     unsigned long native_id) override;

    virtual void render_task_begin(std::string_view name);
    virtual void render_pid_tid(uintptr_t pid, uintptr_t tid) override;
    virtual void render_stack_begin() override;
    virtual void render_python_frame(std::string_view name, std::string_view file, uint64_t line) override;
    virtual void render_native_frame(std::string_view name, std::string_view file, uint64_t line) override;
    virtual void render_cpu_time(microsecond_t cpu_time_us) override;
    virtual void render_stack_end() override;
    virtual bool is_valid() override;
};

} // namespace Datadog
