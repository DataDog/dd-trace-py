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

    // Number of ephemeral entries in the echion StringTable
    std::optional<size_t> string_table_ephemeral_count;

    // Whether fast_copy_memory (ECHION_USE_FAST_COPY_MEMORY) is enabled; unset until the sampler starts
    std::optional<bool> fast_copy_memory_enabled;

    // Number of copy_memory errors accumulated since the last profile reset (i.e. since the last upload)
    size_t copy_memory_error_count = 0;

    // Number of currently tracked allocations in the heap tracker
    std::optional<size_t> heap_tracker_size;

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

    void set_string_table_ephemeral_count(size_t count);
    std::optional<size_t> get_string_table_ephemeral_count() const;

    void set_fast_copy_memory_enabled(bool enabled);
    std::optional<bool> get_fast_copy_memory_enabled() const;

    void add_copy_memory_error_count(size_t count);
    size_t get_copy_memory_error_count() const;

    void set_heap_tracker_size(size_t count);
    std::optional<size_t> get_heap_tracker_size() const;

    // Returns a JSON string containing relevant Profiler Stats to be included
    // in the libdatadog payload.
    std::string get_internal_metadata_json();

    void reset_state();
};

} // namespace Datadog