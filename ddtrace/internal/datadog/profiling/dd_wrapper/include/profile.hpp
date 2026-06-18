#pragma once

#include "constants.hpp"
#include "profiler_stats.hpp"
#include "types.hpp"

#include <atomic>
#include <mutex>
#include <vector>

extern "C"
{
#include "datadog/profiling.h"
}

namespace Datadog {

class ProfileBorrow;

// Outcome of heap_apply_batch, reported per half. The add and sub batches can
// fail independently and have different retry semantics (see heap_apply_batch),
// so the caller needs to know which half succeeded rather than a single bool.
struct HeapApplyResult
{
    bool adds_ok{ false };
    bool subs_ok{ false };
};

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

    // Persistent live-heap profile (Option A).
    //
    // Unlike cur_profile, which is drained and reset on every upload, the heap
    // profile accumulates the *net* set of live sampled allocations across
    // uploads: new allocations are added (ddog_prof_Profile_add2_batch) and
    // freed allocations are subtracted (ddog_prof_Profile_sub2_batch) at export
    // time, and the profile is serialized non-destructively
    // (ddog_prof_Profile_serialize_snapshot) so its accumulated state survives.
    // This avoids re-adding every live allocation under the GIL on each upload.
    //
    // It shares the same sample-type layout and ProfilesDictionary as
    // cur_profile, so a Sample built for cur_profile can be applied to it
    // directly; at upload it is attached as a second pprof and merged with
    // cur_profile by intake. Only created when Heap sampling is enabled.
    ddog_prof_Profile heap_profile{};
    std::mutex heap_profile_mtx{};
    bool heap_enabled{ false };
    // Whether any heap data has actually been applied to heap_profile. Stays
    // false until the memalloc collector applies its first non-empty batch, so
    // CPU/lock/stack-only profilers (whose sample-type layout still includes
    // Heap because type_mask defaults to All) don't emit an empty heap pprof on
    // every upload. Sticky once set: the persistent live set is uploaded every
    // interval thereafter even when no new allocations occurred that interval.
    std::atomic<bool> heap_populated{ false };

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

    // ========================================================================
    // Persistent live-heap profile (Option A)
    // ========================================================================
    // Whether the persistent heap profile is active (Heap sampling enabled and
    // the profile was successfully created).
    bool heap_is_enabled() const { return heap_enabled; }

    // Whether the persistent heap profile has had data applied to it (see
    // heap_populated). Uploaders should gate heap snapshot serialization on
    // this in addition to heap_is_enabled() to avoid emitting empty heap
    // attachments when the memalloc collector never ran.
    bool heap_has_data() const { return heap_populated; }

    // Apply a whole export interval's worth of changes to the persistent heap
    // profile in a single locked pass: `adds` are newly tracked allocations,
    // `subs` are allocations freed since they were last applied. Either array
    // may be empty/null.
    //
    // The two halves are applied add-batch-first under one lock and their
    // outcomes are reported independently because they have very different
    // retry semantics:
    //   - add2_batch is NOT atomic: on the first failing sample it stops and
    //     leaves the already-added samples in the profile, with no way to tell
    //     how many landed. Re-sending the whole add batch would double-count
    //     that prefix, so a failed add half must NOT be retried.
    //   - sub2_batch saturates (subtracting a sample that is absent is a no-op,
    //     values floor at zero), so re-subtracting an already-applied sample is
    //     harmless. A failed sub half can safely be retried on a later export.
    // Warns once per process on the first libdatadog error of each half.
    HeapApplyResult heap_apply_batch(const ddog_prof_Sample2* adds,
                                     size_t n_adds,
                                     const ddog_prof_Sample2* subs,
                                     size_t n_subs);

    // Serialize the persistent heap profile without draining it. Caller owns
    // the returned EncodedProfile and must drop it. Returns a result whose tag
    // is DDOG_PROF_PROFILE_SERIALIZE_RESULT_ERR on failure (or when heap is
    // disabled).
    ddog_prof_Profile_SerializeResult heap_serialize_snapshot();

    // Clear all accumulated state from the persistent heap profile. Used to tie
    // the heap profile's lifetime to the heap tracker: when the tracker is
    // (re)initialized its live set is empty, so the profile must start empty
    // too. Without this, a profiler stop/start (or back-to-back tests in one
    // process) would leak the previous run's live allocations. No-op when heap
    // is disabled.
    bool reset_heap_profile();
};
} // namespace Datadog
