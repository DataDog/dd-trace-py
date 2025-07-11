#pragma once

#include "constants.hpp"
#include "types.hpp"

#include <atomic>
#include <memory>
#include <mutex>
#include <string>
#include <string_view>
#include <vector>

extern "C"
{
#include "datadog/profiling.h"
}

namespace Datadog {

// Serves to collect individual samples, as well as lengthen the scope of string data
class Profile
{
  private:
    // Serialization for static state
    // - string table
    // - ddog_profile
    std::atomic<bool> first_time{ true };
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
    std::vector<ddog_prof_ValueType> samplers{};

    // The profile object is initialized here as a skeleton object, but it
    // cannot be used until it's initialized by libdatadog
    ddog_prof_Profile cur_profile{};

  public:
    // State management
    void one_time_init(SampleType type, unsigned int _max_nframes);
    bool reset_profile();
    void postfork_child();

    // Getters
    size_t get_sample_type_length();
    ddog_prof_Profile& profile_borrow();
    void profile_release();

    // constref getters
    const ValueIndex& val();

    // collect
    bool collect(const ddog_prof_Sample& sample, int64_t endtime_ns);
};
} // namespace Datadog
