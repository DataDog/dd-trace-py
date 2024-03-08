#include "stack_renderer.hpp"
#include "utf8_validate.hpp"

using namespace Datadog;

void
StackRenderer::render_message(std::string_view msg)
{
    // This function is part of the necessary API, but it is unused by the Datadog profiler for now.
    (void)msg;
}

void
StackRenderer::render_thread_begin(PyThreadState* tstate,
                                   std::string_view name,
                                   microsecond_t wall_time_us,
                                   uintptr_t thread_id,
                                   unsigned long native_id)
{
    (void)tstate;
    static bool failed = false;
    if (failed) {
        return;
    }
    sample = ddup_start_sample();
    if (sample == nullptr) {
        std::cerr << "Failed to create a sample.  Stack v2 sampler will be disabled." << std::endl;
        failed = true;
        return;
    }

    //#warning stack_v2 should use a C++ interface instead of re-converting intermediates
    ddup_push_threadinfo(sample, static_cast<int64_t>(thread_id), static_cast<int64_t>(native_id), name);
    ddup_push_walltime(sample, 1000 * wall_time_us, 1);
}

void
StackRenderer::render_stack_begin()
{
    // This function is part of the necessary API, but it is unused by the Datadog profiler for now.
}

void
StackRenderer::render_python_frame(std::string_view name, std::string_view file, uint64_t line)
{
    if (sample == nullptr) {
        std::cerr << "Received a new frame without sample storage.  Some profiling data has been lost." << std::endl;
        return;
    }

    // Normally, further utf-8 validation would be pointless here, but we may be reading data where the
    // string pointer was valid, but the string is actually garbage data at the exact time of the read.
    // This is rare, but blowing some cycles on early validation allows the sample to be retained by
    // libdatadog, so we can evaluate the actual impact of this scenario in live scenarios.
    static const std::string_view invalid = "<invalid_utf8>";
    if (!utf8_check_is_valid(name.data(), name.size())) {
        name = invalid;
    }
    if (!utf8_check_is_valid(file.data(), file.size())) {
        file = invalid;
    }
    ddup_push_frame(sample, name, file, 0, line);
}

void
StackRenderer::render_native_frame(std::string_view name, std::string_view file, uint64_t line)
{
    // This function is part of the necessary API, but it is unused by the Datadog profiler for now.
    (void)name;
    (void)file;
    (void)line;
}

void
StackRenderer::render_cpu_time(microsecond_t cpu_time_us)
{
    if (sample == nullptr) {
        std::cerr << "Received a CPU time without sample storage.  Some profiling data has been lost." << std::endl;
        return;
    }

    // ddup is configured to expect nanoseconds
    ddup_push_cputime(sample, 1000 * cpu_time_us, 1);
}

void
StackRenderer::render_stack_end()
{
    if (sample == nullptr) {
        std::cerr << "Ending a stack without any context.  Some profiling data has been lost." << std::endl;
        return;
    }

    ddup_flush_sample(sample);
    ddup_drop_sample(sample);
    sample = nullptr;
}

bool
StackRenderer::is_valid()
{
    // In general, echion may need to check whether the extension has invalid state before calling into it,
    // but in this case it doesn't matter
    return true;
}
