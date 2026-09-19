#pragma once

#include "constants.hpp"
#include "libdatadog_helpers.hpp"
#include "profiler_stats.hpp"
#include "types.hpp"

#include <cstdint>
#include <mutex>
#include <optional>
#include <vector>

namespace Datadog {

struct ProfileBorrow;

// Owns the active libdatadog Profile and serializes sample collection under profile_mtx.
class Profile
{
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

    // Active libdatadog profile. Created during initialization and recreated after
    // reset/fork; empty when initialization fails or after cleanup.
    std::optional<rust::Box<ddprof::Profile>> cur_profile{};
    Datadog::ProfilerStats cur_profiler_stats{};

    void one_time_init_impl(SampleType type, unsigned int _max_nframes);

  public:
    // State management
    bool one_time_init(SampleType type, unsigned int _max_nframes);
    void cleanup();
    void prefork();
    void postfork_parent();
    bool postfork_child(bool recreate_profile = true);

    // Getters
    size_t get_sample_type_length();

    // Returns nullopt if the profile is not available (not initialized or cleaned up).
    // The check is performed under profile_mtx, synchronized with cleanup().
    std::optional<ProfileBorrow> borrow();

    // constref getters
    const ValueIndex& val();

    // collect
    bool collect(const ddprof::DictionarySample& sample, int64_t endtime_ns);
};
} // namespace Datadog
