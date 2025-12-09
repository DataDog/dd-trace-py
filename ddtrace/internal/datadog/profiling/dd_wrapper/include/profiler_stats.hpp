#pragma once

#include <chrono>
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

    using point_in_time = std::chrono::time_point<std::chrono::steady_clock, std::chrono::nanoseconds>;

    // The time the Sampler started collecting data for this Profile
    std::optional<point_in_time> profile_start;

    // The time the Sampler finished collecting data for the last Sample of this Profile
    std::optional<point_in_time> profile_end;

  public:
    ProfilerStats() = default;
    ~ProfilerStats() = default;

    void increment_sample_count(size_t k_sample_count = 1);
    size_t get_sample_count();

    void increment_sampling_event_count(size_t k_sampling_event_count = 1);
    size_t get_sampling_event_count();

    void set_profile_start_if_unset();
    void set_profile_end();
    std::optional<std::chrono::duration<unsigned long long, std::nano>> get_profile_duration();

    // Returns a JSON string containing relevant Profiler Stats to be included
    // in the libdatadog payload.
    std::string get_internal_metadata_json();

    void reset_state();
};

} // namespace Datadog