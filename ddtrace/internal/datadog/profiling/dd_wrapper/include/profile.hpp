#pragma once

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
    std::atomic<long> g_profile_seq{ 0 };
    std::mutex profile_mtx{};
    std::atomic<pid_t> current_pid{ 0 };

    // Storage for strings
    std::deque<std::string> string_storage{};
    StringTable strings{};
    std::mutex string_table_mtx{};

    // Configuration
    SampleType type_mask{ 0 };
    unsigned int max_nframes{ 128 };
    ddog_prof_Period default_period{};

    // Lookup for values
    ValueIndex val_idx{};

    // Configuration for the pprof exporter
    std::vector<ddog_prof_ValueType> samplers{};

    // Intermediate storage; this gets flushed to the exporter
    // when it's time to upload.
    ddog_prof_Profile cur_profile{};
    ddog_prof_Profile last_profile{};

    // Global state
    inline static std::atomic<bool> dirty;

  public:
    void one_time_init(SampleType type, unsigned int _max_nframes);
    void setup_samplers();
    bool cycle_buffers();
    void entrypoint_check();

    // Getters
    unsigned int get_max_nframes();
    SampleType get_type_mask();
    size_t get_sample_type_length();
    long get_profile_seq();
    ddog_prof_Profile& get_current_profile();

    // String table manipulation
    std::string_view insert_or_get(std::string_view sv);

    // Value index getter
    const ValueIndex& val();

    // collect
    bool collect(const ddog_prof_Sample& sample);

    // Global manipulators
    static void mark_dirty();
};
} // namespace Datadog
