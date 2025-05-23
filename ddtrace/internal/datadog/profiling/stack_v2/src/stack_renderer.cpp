#include "stack_renderer.hpp"

#include "thread_span_links.hpp"

#include "echion/strings.h"

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

    // Get the current time in ns in a way compatible with python's time.monotonic_ns(), which is backed by
    // clock_gettime(CLOCK_MONOTONIC) on linux and mach_absolute_time() on macOS.
    // This is not the same as std::chrono::steady_clock, which is backed by clock_gettime(CLOCK_MONOTONIC_RAW)
    // (although this is underspecified in the standard)
    int64_t now_ns = 0;
    timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) == 0) {
        now_ns = static_cast<int64_t>(ts.tv_sec) * 1'000'000'000LL + static_cast<int64_t>(ts.tv_nsec);
        ddup_push_monotonic_ns(sample, now_ns);
    }

    // Save the thread information in case we observe a task on the thread
    thread_state.id = thread_id;
    thread_state.native_id = native_id;
    thread_state.name = std::string(name);
    thread_state.now_time_ns = now_ns;
    thread_state.wall_time_ns = 1000LL * wall_time_us;
    thread_state.cpu_time_ns = 0; // Walltime samples are guaranteed, but CPU times are not. Initialize to 0
                                  // since we don't know if we'll get a CPU time here.

    pushed_task_name = false;

    // Finalize the thread information we have
    ddup_push_threadinfo(sample, static_cast<int64_t>(thread_id), static_cast<int64_t>(native_id), name);
    ddup_push_walltime(sample, thread_state.wall_time_ns, 1);

    const std::optional<Span> active_span = ThreadSpanLinks::get_instance().get_active_span_from_thread_id(thread_id);
    if (active_span) {
        ddup_push_span_id(sample, active_span->span_id);
        ddup_push_local_root_span_id(sample, active_span->local_root_span_id);
        ddup_push_trace_type(sample, std::string_view(active_span->span_type));
    }
}

void
StackRenderer::render_task_begin(std::string task_name, bool on_cpu)
{
    static bool failed = false;
    if (failed) {
        return;
    }
    if (sample == nullptr) {
        // The very first task on a thread will already have a sample, since there's no way to deduce whether
        // a thread has tasks without checking, and checking before populating the sample would make the state
        // management very complicated.  The rest of the tasks will not have samples and will hit this code path.
        sample = ddup_start_sample();
        if (sample == nullptr) {
            std::cerr << "Failed to create a sample.  Stack v2 sampler will be disabled." << std::endl;
            failed = true;
            return;
        }

        // Add the thread context into the sample
        ddup_push_threadinfo(sample,
                             static_cast<int64_t>(thread_state.id),
                             static_cast<int64_t>(thread_state.native_id),
                             thread_state.name);
        ddup_push_task_name(sample, task_name);
        ddup_push_walltime(sample, thread_state.wall_time_ns, 1);
        if (on_cpu)
            ddup_push_cputime(sample, thread_state.cpu_time_ns, 1); // initialized to 0, so possibly a no-op
        ddup_push_monotonic_ns(sample, thread_state.now_time_ns);

        // We also want to make sure the tid -> span_id mapping is present in the sample for the task
        const std::optional<Span> active_span =
          ThreadSpanLinks::get_instance().get_active_span_from_thread_id(thread_state.id);
        if (active_span) {
            ddup_push_span_id(sample, active_span->span_id);
            ddup_push_local_root_span_id(sample, active_span->local_root_span_id);
            ddup_push_trace_type(sample, std::string_view(active_span->span_type));
        }

        pushed_task_name = false;
    }
}

void
StackRenderer::render_stack_begin(long long, long long, const std::string&)
{
    // This function is part of the necessary API, but it is unused by the Datadog profiler for now.
}

void
StackRenderer::render_frame(Frame& frame)
{
    if (sample == nullptr) {
        std::cerr << "Received a new frame without sample storage. Some profiling data has been lost." << std::endl;
        return;
    }

    // Ordinarily we could just call frame_cache->lookup() here, but our
    // underlying frame is owned by the LRUCache, which may have cleaned it up,
    // causing the table keys to be garbage.  Since individual frames in
    // the stack may be bad, this isn't a failable condition.  Instead, populate
    // some defaults.
    static constexpr std::string_view missing_filename = "<unknown file>";
    static constexpr std::string_view missing_name = "<unknown function>";
    std::string_view filename_str;
    std::string_view name_str;
    try {
        filename_str = string_table.lookup(frame.filename);
    } catch (StringTable::Error&) {
        filename_str = missing_filename;
    }

    try {
        name_str = string_table.lookup(frame.name);
    } catch (StringTable::Error&) {
        name_str = missing_name;
    }

    auto line = frame.location.line;

    ddup_push_frame(sample, name_str, filename_str, 0, line);
}

void
StackRenderer::render_cpu_time(uint64_t cpu_time_us)
{
    if (sample == nullptr) {
        std::cerr << "Received a CPU time without sample storage.  Some profiling data has been lost." << std::endl;
        return;
    }

    // TODO - it's absolutely false that thread-level CPU time is task time.  This needs to be normalized
    // to the task level, but for now just keep it because this is how the v1 sampler works
    thread_state.cpu_time_ns = 1000LL * cpu_time_us;
    ddup_push_cputime(sample, thread_state.cpu_time_ns, 1);
}

void
StackRenderer::render_stack_end(MetricType, uint64_t)
{
    if (sample == nullptr) {
        std::cerr << "Ending a stack without any context.  Some profiling data has been lost." << std::endl;
        return;
    }

    ddup_flush_sample_v2(sample);
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
