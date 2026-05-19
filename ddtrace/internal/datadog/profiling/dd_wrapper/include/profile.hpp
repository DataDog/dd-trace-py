#pragma once

#include "constants.hpp"
#include "profiler_stats.hpp"
#include "types.hpp"

#include <mutex>
#include <vector>

extern "C"
{
#include "datadog/profiling.h"
}

namespace Datadog {

class ProfileBorrow;

// Serves to collect individual samples, as well as lengthen the scope of string data
class Profile
{
    friend class ProfileBorrow;

  private:
    // Serialization for static state
    // - string table
    // - ddog_profile
    std::once_flag init_once{};
    std::mutex profile_mtx{};

    // Configuration
    SampleType type_mask{ 0 };
    unsigned int max_nframes{ g_default_max_nframes };
    ddog_prof_Period default_period{};

    // Sampler setup
    void setup_samplers();

    // Lookup for values
    ValueIndex val_idx{};

    // Configuration for the pprof exporter
    std::vector<ddog_prof_SampleType> samplers{};

    // The profile object is initialized here as a skeleton object, but it
    // cannot be used until it's initialized by libdatadog
    ddog_prof_Profile cur_profile{};
    Datadog::ProfilerStats cur_profiler_stats{};

    // Internal access methods - not for direct use
    ddog_prof_Profile& profile_borrow_internal();
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

    // collect
    bool collect(const ddog_prof_Sample2& sample, int64_t endtime_ns);
};
} // namespace Datadog
