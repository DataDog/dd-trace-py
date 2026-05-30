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
class Sample;

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

    // Maximum pending samples per thread before auto-flush.
    static constexpr size_t k_batch_threshold = 8;

    // Drain the calling thread's batch under already-held profile_mtx.
    void drain_tls_locked_();

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

    // Batched collect: moves data out of `s` into a thread-local batch.
    // Auto-flushes once the batch reaches k_batch_threshold. After this call
    // the Sample's vectors/arena are in a moved-from state and must be
    // re-initialized by the caller (Sample::flush_sample handles this).
    bool buffered_collect(Sample& s);

    // Drain the calling thread's batch (acquires profile_mtx once).
    void flush_thread_batch();

    // Drain every registered thread's batch (acquires profile_mtx once).
    // Called before upload/serialize and around fork().
    void flush_all_thread_batches();

    // Toggle batched collect on/off at runtime. When disabled,
    // buffered_collect() falls back to sync collect (one Profile_add2 call
    // per sample). Used by the microbench and as a kill-switch.
    static void set_batching_enabled(bool enabled);
    static bool batching_enabled();
};
} // namespace Datadog
