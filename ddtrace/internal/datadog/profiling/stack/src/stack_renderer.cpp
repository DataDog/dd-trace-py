#include "stack_renderer.hpp"

#include "sampler.hpp"
#include "thread_span_links.hpp"

#include "dd_wrapper/include/clock.hpp"
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

    // See clock.hpp for platform-specific details.
    const int64_t now_ns = get_monotonic_ns();
    sample->push_monotonic_ns(now_ns);

    // Save the thread information in case we observe a task on the thread
    thread_state.id = thread_id;
    thread_state.native_id = native_id;
    thread_state.name = std::string(name);
    thread_state.now_time_ns = now_ns;
    thread_state.wall_time_ns = 1000 * wall_time_us;
    thread_state.cpu_time_ns = 0;
    thread_state.has_cpu_time = false;
    thread_state.task_on_cpu = true;
    thread_state.top_frame_name.clear();

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
StackRenderer::render_task_begin(std::string_view task_name, bool on_cpu)
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
    thread_state.task_on_cpu = on_cpu;
    thread_state.top_frame_name.clear();
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

    if (thread_state.top_frame_name.empty()) {
        auto maybe_top_name = string_table.lookup(frame.name);
        if (maybe_top_name) {
            thread_state.top_frame_name = std::string(maybe_top_name->get());
        }
    }

    auto line = frame.line;

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
StackRenderer::render_native_frame(const std::string& name, const std::string& module)
{
    if (sample == nullptr) {
        return;
    }

    if (thread_state.top_frame_name.empty()) {
        thread_state.top_frame_name = name;
    }

    auto maybe_name_id = Datadog::intern_string(name);
    if (!maybe_name_id) {
        return;
    }
    auto name_id = *maybe_name_id;

    auto maybe_filename_id = Datadog::intern_string(module);
    if (!maybe_filename_id) {
        return;
    }
    auto filename_id = *maybe_filename_id;

    // Reuse the same function_id_cache as render_frame to avoid redundant intern_function calls
    function_id fid;
    auto cached = function_id_cache.find({ name_id, filename_id });
    if (cached == function_id_cache.end()) {
        auto maybe_fid = Datadog::intern_function(name_id, filename_id);
        if (!maybe_fid) {
            return;
        }
        fid = *maybe_fid;
        function_id_cache.insert({ { static_cast<void*>(name_id), static_cast<void*>(filename_id) }, fid });
    } else {
        fid = cached->second;
    }

    sample->push_frame(fid, 1, 0);
}

void
StackRenderer::render_cpu_time(microsecond_t cpu_time_us)
{
    if (sample == nullptr) {
        std::cerr << "Received a CPU time without sample storage.  Some profiling data has been lost." << std::endl;
        return;
    }

    // TODO- thread-level CPU time is attributed to the first task on a thread (whichever task is
    // current when render_cpu_time fires).  Per-task CPU time would require intercepting every asyncio/greenlet
    // context switch and diffing the thread CPU clock at each switch -- a separate effort.
    thread_state.cpu_time_ns = 1000 * cpu_time_us;
    thread_state.has_cpu_time = true;
    sample->push_cputime(thread_state.cpu_time_ns, 1);
}

// Classify the off-CPU cause from the leaf Python or native frame name.
// Matches Python-level blocking patterns only; OS-level causes (preemption,
// page faults) are indistinguishable here and fall through to "other".
static std::string_view
classify_offcpu_cause(std::string_view frame_name)
{
    if (frame_name.find("sleep") != std::string_view::npos) {
        return "sleep";
    }
    if (frame_name.find("acquire") != std::string_view::npos || frame_name.find("wait") != std::string_view::npos) {
        return "lock";
    }
    if (frame_name.find("recv") != std::string_view::npos || frame_name.find("send") != std::string_view::npos) {
        return "io";
    }
    return "other";
}

void
StackRenderer::render_stack_end()
{
    if (sample == nullptr) {
        std::cerr << "Ending a stack without any context.  Some profiling data has been lost." << std::endl;
        return;
    }

    // Off-CPU approximation.  Only emit when CPU time was actually measured
    // (has_cpu_time distinguishes "measured 0" from "unavailable").
    if (thread_state.has_cpu_time) {
        int64_t off_cpu_ns;
        if (thread_state.task_on_cpu) {
            // wall - cpu, clamped to 0 for clock skew between the two measurement clocks.
            off_cpu_ns = thread_state.wall_time_ns > thread_state.cpu_time_ns
                           ? thread_state.wall_time_ns - thread_state.cpu_time_ns
                           : 0;
        } else {
            // Suspended task: was off-CPU for the full sampling interval.
            off_cpu_ns = thread_state.wall_time_ns;
        }
        if (sample->push_offcputime(off_cpu_ns, 1) && off_cpu_ns > 0) {
            sample->push_label(ExportLabelKey::off_cpu_cause, classify_offcpu_cause(thread_state.top_frame_name));
        }
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
    // Use placement new instead of clear because the sampling thread may
    // have been mid-rehash on either cache when fork was called.
    // Traversing the buckets to free nodes would crash if they were left
    // in an inconsistent state.
    new (&string_id_cache) std::unordered_map<StringTable::Key, string_id>();
    new (&function_id_cache)
      std::unordered_map<internal::PtrPair, function_id, internal::PtrPairHash, internal::PtrPairEq>();
    sample = nullptr;
}
