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
    SampleType type_mask{ SampleType::All };
    unsigned int max_nframes{ g_default_max_nframes };
    ddog_prof_Period default_period{};

    // Sampler setup
    void setup_samplers();

    // Lookup for values
    ValueIndex val_idx{};

    // Configuration for the pprof exporter
    std::vector<ddog_prof_ValueType> samplers{};

    // These are initialized here as skeleton objects, but they cannot be used until
    // they're initialized by libdatadog
    ddog_prof_Profile cur_profile{};
    ddog_prof_Profile last_profile{};



  public:
    // State management
    void one_time_init(SampleType type, unsigned int _max_nframes);
    void one_time_init_impl(SampleType type, unsigned int _max_nframes);
    bool cycle_buffers();
    void reset();

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
