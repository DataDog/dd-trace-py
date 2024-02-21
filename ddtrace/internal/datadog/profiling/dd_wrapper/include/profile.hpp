#pragma once

#include "constants.hpp"
#include "types.hpp"

#include <atomic>
#include <deque>
#include <memory>
#include <mutex>
#include <string>
#include <string_view>
#include <unordered_set>
#include <vector>

extern "C"
{
#include "datadog/profiling.h"
}

namespace Datadog {

// Unordered containers don't get heterogeneous lookup until gcc-10, so for now use this
// strategy to dedup + store strings.
using StringTable = std::unordered_set<std::string_view>;

// Serves to collect individual samples, as well as lengthen the scope of string data
class Profile
{
  private:
    // Serialization for static state
    // - string table
    // - ddog_profile
    std::atomic<bool> first_time{ true };
    std::mutex profile_mtx{};

    // Storage for strings
    std::deque<std::string> string_storage{};
    StringTable strings{};
    std::mutex string_table_mtx{};

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

    // These are initialized here as skeleton objects, but they cannot be used until
    // they're initialized by libdatadog
    ddog_prof_Profile cur_profile{};
    ddog_prof_Profile last_profile{};

  public:
    // State management
    void one_time_init(SampleType type, unsigned int _max_nframes);
    bool cycle_buffers();
    void reset();
    void postfork_child();

    // Getters
    size_t get_sample_type_length();
    ddog_prof_Profile& profile_borrow();
    void profile_release();

    // String table manipulation
    std::string_view insert_or_get(std::string_view sv);

    // constref getters
    const ValueIndex& val();

    // collect
    bool collect(const ddog_prof_Sample& sample);
};
} // namespace Datadog
