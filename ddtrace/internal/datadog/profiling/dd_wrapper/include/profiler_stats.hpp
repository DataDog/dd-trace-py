#pragma once

#include <cstddef>
#include <optional>
#include <string>

namespace Datadog {

/*
ProfilerStats holds statistics around Profiling to be sent along
with the actual Profiles.

None of its methods are thread-safe and it should typically used with
a mutex to protect access to the data.
*/
class ProfilerStats
{
  private:
    // Number of samples collected (one per thread)
    size_t sample_count = 0;

    // Number of sampling events (one per collection cycle)
    size_t sampling_event_count = 0;

    // The latest sampling interval (in microseconds) as determined by adaptive sampling
    std::optional<size_t> sampling_interval_us;

    // Number of entries in the echion StringTable
    std::optional<size_t> string_table_count;

    // Whether fast_copy_memory (safe_memcpy) is enabled; unset until the sampler starts
    std::optional<bool> fast_copy_memory_enabled;

    // User opted out of fast copy (env var or set_fast_copy(false)); static per process
    std::optional<bool> fast_copy_memory_user_disabled;

    // Whether safe_memcpy initialized at startup; static per process
    std::optional<bool> fast_copy_memory_capable;

    // Sticky: fell back to syscall copy (init failure, foreign handler, etc.)
    std::optional<bool> fast_copy_memory_syscall_fallback;

    // Number of copy_memory errors accumulated since the last profile reset (i.e. since the last upload)
    size_t copy_memory_error_count = 0;

    // Number of currently tracked allocations in the heap tracker
    std::optional<size_t> heap_tracker_size;

    // Samples dropped because the cap was reached (cumulative over tracker lifetime)
    std::optional<size_t> heap_tracker_cap_drops;

    // Peak number of asyncio tasks seen across sampled threads in any single sampling
    // cycle during the current profile period (see set_asyncio_task_count).
    std::optional<size_t> asyncio_task_count;

    // Number of greenlets currently tracked by the stack profiler
    std::optional<size_t> greenlet_count;

    // Total CPU time (in microseconds) spent by the sampler thread capturing samples
    size_t sample_capture_cpu_time_us = 0;

  public:
    ProfilerStats() = default;
    ~ProfilerStats() = default;

    void increment_sample_count(size_t k_sample_count = 1);
    size_t get_sample_count() const;

    void increment_sampling_event_count(size_t k_sampling_event_count = 1);
    size_t get_sampling_event_count() const;

    void set_sampling_interval_us(size_t interval_us);
    std::optional<size_t> get_sampling_interval_us() const;

    void set_string_table_count(size_t count);
    std::optional<size_t> get_string_table_count() const;

    void set_fast_copy_memory_enabled(bool enabled);
    std::optional<bool> get_fast_copy_memory_enabled() const;

    void set_fast_copy_memory_user_disabled(bool disabled);
    std::optional<bool> get_fast_copy_memory_user_disabled() const;

    void set_fast_copy_memory_capable(bool capable);
    std::optional<bool> get_fast_copy_memory_capable() const;

    void set_fast_copy_memory_syscall_fallback(bool fallback);
    std::optional<bool> get_fast_copy_memory_syscall_fallback() const;

    // fast_copy_memory_* are process-static; carry them across ProfilerStats swaps.
    void copy_fast_copy_metadata_from(const ProfilerStats& other);

    void add_copy_memory_error_count(size_t count);
    size_t get_copy_memory_error_count() const;

    void set_heap_tracker_size(size_t count);
    std::optional<size_t> get_heap_tracker_size() const;

    void set_heap_tracker_cap_drops(size_t count);
    std::optional<size_t> get_heap_tracker_cap_drops() const;

    void set_asyncio_task_count(size_t count);
    std::optional<size_t> get_asyncio_task_count() const;

    void set_greenlet_count(size_t count);
    std::optional<size_t> get_greenlet_count() const;

    void add_sample_capture_cpu_time_us(size_t cpu_time_us);
    size_t get_sample_capture_cpu_time_us() const;

    // Returns a JSON string containing relevant Profiler Stats to be included
    // in the libdatadog payload.
    std::string get_internal_metadata_json();

    void reset_state();
};

} // namespace Datadog