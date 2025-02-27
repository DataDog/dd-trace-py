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

#include "echion/frame.h"
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
    // Whether task name has been pushed for the current sample. Whenever
    // the sample is created, this has to be reset.
    bool pushed_task_name = false;

    void open() override {}
    void close() override {}
    void header() override {}
    void metadata(const std::string&, const std::string&) override {}
    void frame(mojo_ref_t, mojo_ref_t, mojo_ref_t, mojo_int_t, mojo_int_t, mojo_int_t, mojo_int_t) override{};
    void frame_ref(mojo_ref_t) override{};
    void frame_kernel(const std::string&) override {};
    void string(mojo_ref_t, const std::string&) override {};
    void string_ref(mojo_ref_t) override{};

    virtual void render_message(std::string_view msg) override;
    virtual void render_thread_begin(PyThreadState* tstate,
                                     std::string_view name,
                                     microsecond_t wall_time_us,
                                     uintptr_t thread_id,
                                     unsigned long native_id) override;
    virtual void render_task_begin() override;
    virtual void render_stack_begin(long long pid, long long iid, const std::string& name) override;
    virtual void render_frame(Frame& frame) override;
    virtual void render_cpu_time(uint64_t cpu_time_us) override;
    virtual void render_stack_end(MetricType metric_type, uint64_t value) override;
    virtual bool is_valid() override;
};

} // namespace Datadog
