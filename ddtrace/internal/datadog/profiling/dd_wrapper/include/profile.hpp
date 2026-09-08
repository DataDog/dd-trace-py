#pragma once

#include "constants.hpp"
#include "libdatadog_helpers.hpp"
#include "profiler_stats.hpp"
#include "types.hpp"

#include <cstdint>
#include <mutex>
#include <optional>
#include <string_view>
#include <vector>

namespace Datadog {

class ProfileBorrow;

// Serves to collect individual samples, as well as lengthen the scope of string data
class Profile
{
    friend class ProfileBorrow;

  private:
    // Serialization for static state
    // - string table
    // - profile
    std::once_flag init_once{};
    std::mutex profile_mtx{};

    // Configuration
    SampleType type_mask{ 0 };
    unsigned int max_nframes{ g_default_max_nframes };
    ddprof::Period default_period{};

    // Sampler setup
    void setup_samplers();

    // Lookup for values
    ValueIndex val_idx{};

    // Configuration for the pprof exporter
    std::vector<ddprof::SampleType> samplers{};

    // The profile object is initialized here as a skeleton object, but it
    // cannot be used until it's initialized by libdatadog
    std::optional<rust::Box<ddprof::Profile>> cur_profile{};
    Datadog::ProfilerStats cur_profiler_stats{};

    // Internal access methods - not for direct use
    ddprof::Profile& profile_borrow_internal();
    void profile_release();

    void one_time_init_impl(SampleType type, unsigned int _max_nframes);

  public:
    // State management
    void one_time_init(SampleType type, unsigned int _max_nframes);
    bool reset_profile();
    void cleanup();
    void prefork();
    void postfork_parent();
    void postfork_child();

    // Getters
    size_t get_sample_type_length();

    ProfileBorrow borrow();

    // constref getters
    const ValueIndex& val();

    std::vector<std::uint8_t> serialize_to_vec();
    bool add_endpoint(std::int64_t local_root_span_id, std::string_view endpoint);
    bool add_endpoint_count(std::string_view endpoint, std::int64_t value);

    // collect
    bool collect(const ddprof::Sample2& sample, int64_t endtime_ns);
};
} // namespace Datadog
