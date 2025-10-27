#include "stack_renderer.hpp"

#include "thread_span_links.hpp"

#include "echion/strings.h"

#include <unordered_map>

// Forward declare ddup interface functions
extern "C"
{
    uint32_t ddup_intern_string(std::string_view str);
    void ddup_push_frame_ids(Datadog::Sample* sample,
                             uint32_t name_id,
                             uint32_t filename_id,
                             uint64_t address,
                             int64_t line);
}

using namespace Datadog;

// Thread-local cache for StringTable::Key → ddog_prof_ManagedStringId
// This avoids costly string_table lookups + libdatadog interning by caching the final IDs
namespace {
struct StringTableKeyCache
{
    static constexpr size_t MAX_CACHE_SIZE = 10000;
    std::unordered_map<StringTable::Key, uint32_t> cache;

    uint32_t get_or_intern(StringTable::Key key)
    {
        // Fast path: check if we've already interned this key
        auto it = cache.find(key);
        if (it != cache.end()) {
            return it->second;
        }

        // Slow path: look up the string in the string table and intern it to libdatadog
        auto maybe_str = string_table.lookup(key);
        if (!maybe_str) {
            // Key not found in string table, return invalid ID
            return 0;
        }

        std::string_view str = maybe_str->get();

        // Intern the string to libdatadog and get the ID
        auto id = ddup_intern_string(str);

        // Add to cache if not too large and ID is valid
        if (id != 0 && cache.size() < MAX_CACHE_SIZE) {
            cache.emplace(key, id);
        }

        return id;
    }
};

static StringTableKeyCache&
get_string_table_key_cache()
{
    thread_local StringTableKeyCache cache;
    return cache;
}
} // anonymous namespace

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

        pushed_task_name = true;
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

    auto line = frame.location.line;

    auto& cache = get_string_table_key_cache();

    // DEV: Echion pushes a dummy frame containing task name, and its line
    // number is set to 0.
    if (line == 0) {
        if (!pushed_task_name) {
            // For task names, we still need to look up the string since ddup_push_task_name takes string_view
            auto name_id = cache.get_or_intern(frame.name);
            ddup_push_task_name_id(sample, name_id);
            pushed_task_name = true;
        }
        // And return early to avoid pushing task name as a frame
        return;
    }

    // Optimized path: use cached Key → ManagedStringId mapping to avoid costly lookups
    // Cache lookups are fast, and string_table lookups + libdatadog interning only happen once per unique Key
    auto name_id = cache.get_or_intern(frame.name);
    auto filename_id = cache.get_or_intern(frame.filename);

    // Push the frame with pre-interned IDs (no string lookups or re-interning needed!)
    ddup_push_frame_ids(sample, name_id, filename_id, 0, line);
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
