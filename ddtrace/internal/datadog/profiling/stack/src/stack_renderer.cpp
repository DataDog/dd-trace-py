#include "stack_renderer.hpp"

#include "sampler.hpp"
#include "thread_span_links.hpp"

#include "dd_wrapper/include/sample_manager.hpp"

#include "echion/echion_sampler.h"
#include "echion/strings.h"
#include <ddup_interface.hpp>
#include <unordered_map>

using namespace Datadog;

void
StackRenderer::render_thread_begin(PyThreadState* tstate,
                                   std::string_view name,
                                   int64_t wall_time_us,
                                   uintptr_t thread_id,
                                   unsigned long native_id)
{
    (void)tstate;
    static bool failed = false;
    if (failed) {
        return;
    }
    sample = SampleManager::start_sample();
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
        sample->push_monotonic_ns(now_ns);
    }

    // Save the thread information in case we observe a task on the thread
    thread_state.id = thread_id;
    thread_state.native_id = native_id;
    thread_state.name = std::string(name);
    thread_state.now_time_ns = now_ns;
    thread_state.wall_time_ns = 1000 * wall_time_us;
    thread_state.cpu_time_ns = 0; // Walltime samples are guaranteed, but CPU times are not. Initialize to 0
                                  // since we don't know if we'll get a CPU time here.

    pushed_task_name = false;

    // Finalize the thread information we have
    sample->push_threadinfo(static_cast<int64_t>(thread_id), static_cast<int64_t>(native_id), name);
    sample->push_walltime(thread_state.wall_time_ns, 1);

    const std::optional<Span> active_span = ThreadSpanLinks::get_instance().get_active_span_from_thread_id(thread_id);
    if (active_span) {
        sample->push_span_id(active_span->span_id);
        sample->push_local_root_span_id(active_span->local_root_span_id);
        sample->push_trace_type(std::string_view(active_span->span_type));
    }
}

void
StackRenderer::render_task_begin(const std::string& task_name, bool on_cpu)
{
    static bool failed = false;
    if (failed) {
        return;
    }

    if (sample == nullptr) {
        // The very first task on a thread will already have a sample, since there's no way to deduce whether
        // a thread has tasks without checking, and checking before populating the sample would make the state
        // management very complicated.  The rest of the tasks will not have samples and will hit this code path.
        sample = SampleManager::start_sample();
        if (sample == nullptr) {
            std::cerr << "Failed to create a sample.  Stack v2 sampler will be disabled." << std::endl;
            failed = true;
            return;
        }

        // Add the thread context into the sample
        sample->push_threadinfo(
          static_cast<int64_t>(thread_state.id), static_cast<int64_t>(thread_state.native_id), thread_state.name);
        sample->push_walltime(thread_state.wall_time_ns, 1);

        if (on_cpu) {
            // initialized to 0, so possibly a no-op
            sample->push_cputime(thread_state.cpu_time_ns, 1);
        }

        sample->push_monotonic_ns(thread_state.now_time_ns);

        // We also want to make sure the tid -> span_id mapping is present in the sample for the task
        const std::optional<Span> active_span =
          ThreadSpanLinks::get_instance().get_active_span_from_thread_id(thread_state.id);
        if (active_span) {
            sample->push_span_id(active_span->span_id);
            sample->push_local_root_span_id(active_span->local_root_span_id);
            sample->push_trace_type(std::string_view(active_span->span_type));
        }
    }

    sample->push_task_name(task_name);
    pushed_task_name = true;
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

    const auto& string_table = Sampler::get().get_echion().string_table();

    auto line = frame.location.line;

    string_id name_id;
    auto maybe_name_id = string_id_cache.find(frame.name);
    if (maybe_name_id == string_id_cache.end()) {
        std::string_view name_str;
        auto maybe_name_str = string_table.lookup(frame.name);
        if (maybe_name_str) {
            name_str = maybe_name_str->get();
        } else {
            name_str = missing_name;
        }

        auto maybe_interned_name_id = Datadog::intern_string(name_str);
        if (!maybe_interned_name_id) {
            return;
        }
        name_id = *maybe_interned_name_id;
        string_id_cache.insert({ frame.name, name_id });
    } else {
        name_id = maybe_name_id->second;
    }

    // DEV: Echion pushes a dummy frame containing task name, and its line
    // number is set to 0.
    if (line == 0) {
        if (!pushed_task_name) {
            std::string_view name_str;
            auto maybe_name_str = string_table.lookup(frame.name);
            if (maybe_name_str) {
                name_str = maybe_name_str->get();
            } else {
                name_str = missing_name;
            }

            sample->push_task_name(name_str);
            pushed_task_name = true;
        }
        // And return early to avoid pushing task name as a frame
        // TODO: We may want to do that for clarity, actually. Let's reconvene.
        return;
    }

    string_id filename_id;
    auto maybe_filename_id = string_id_cache.find(frame.filename);
    if (maybe_filename_id == string_id_cache.end()) {
        std::string_view filename_str;
        auto maybe_filename_str = string_table.lookup(frame.filename);
        if (maybe_filename_str) {
            filename_str = maybe_filename_str->get();
        } else {
            filename_str = missing_filename;
        }

        auto maybe_interned_filename_id = Datadog::intern_string(filename_str);
        if (!maybe_interned_filename_id) {
            return;
        }
        filename_id = *maybe_interned_filename_id;
        string_id_cache.insert({ frame.filename, filename_id });
    } else {
        filename_id = maybe_filename_id->second;
    }

    function_id function_id;
    auto maybe_function_id = function_id_cache.find({ name_id, filename_id });
    if (maybe_function_id == function_id_cache.end()) {
        auto maybe_interned_function_id = Datadog::intern_function(name_id, filename_id);
        if (!maybe_interned_function_id) {
            return;
        }
        function_id = *maybe_interned_function_id;
        function_id_cache.insert({ { static_cast<void*>(name_id), static_cast<void*>(filename_id) }, function_id });
    } else {
        function_id = maybe_function_id->second;
    }

    sample->push_frame(function_id, 0, line);
}

void
StackRenderer::render_cpu_time(microsecond_t cpu_time_us)
{
    if (sample == nullptr) {
        std::cerr << "Received a CPU time without sample storage.  Some profiling data has been lost." << std::endl;
        return;
    }

    // TODO - it's absolutely false that thread-level CPU time is task time.  This needs to be normalized
    // to the task level, but for now just keep it because this is how the v1 sampler works
    thread_state.cpu_time_ns = 1000 * cpu_time_us;
    sample->push_cputime(thread_state.cpu_time_ns, 1);
}

void
StackRenderer::render_stack_end()
{
    if (sample == nullptr) {
        std::cerr << "Ending a stack without any context.  Some profiling data has been lost." << std::endl;
        return;
    }

    sample->flush_sample();
    SampleManager::drop_sample(sample);
    sample = nullptr;
}

Datadog::StackRenderer::StackRenderer()
{
    function_id_cache.reserve(100'000);
    function_id_cache.max_load_factor(0.7f);
    string_id_cache.reserve(100'000);
    string_id_cache.max_load_factor(0.7f);
}

void
Datadog::StackRenderer::postfork_child()
{
    // Clear the caches to avoid using stale interned string/function IDs
    // from the parent process's dictionary
    string_id_cache.clear();
    function_id_cache.clear();
}
