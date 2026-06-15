#include "profile.hpp"

#include "libdatadog_helpers.hpp"
#include "profile_borrow.hpp"
#include "profiler_state.hpp"
#include "profiler_stats.hpp"

#include <datadog/profiling.h>
#include <iostream>

#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

// Inline helpers
namespace {

inline bool
make_profile(const ddog_prof_Slice_SampleType& sample_types,
             const struct ddog_prof_Period* period,
             ddog_prof_Profile& profile)
{
    // Private helper function for creating a ddog_prof_Profile from arguments

    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    auto maybe_dict = Datadog::ProfilerState::get().get_profiles_dictionary();
    if (!maybe_dict) {
        return false;
    }

    auto& dict = maybe_dict.value();
    auto res = ddog_prof_Profile_with_dictionary(&profile, &dict, sample_types, period);
    if (res.flags) { // NOLINT (cppcoreguidelines-pro-type-union-access)
        if (!already_warned) {
            already_warned = true;
            const std::string errmsg = std::string(res.err);
            std::cerr << errmsg << std::endl;
        }
        return false;
    }
    return true;
}

}

bool
Datadog::Profile::reset_profile()
{
    const std::lock_guard<std::mutex> lock(profile_mtx);
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety

    // Clear the profile before using it
    auto res = ddog_prof_Profile_reset(&cur_profile);
    if (!res.ok) {          // NOLINT (cppcoreguidelines-pro-type-union-access)
        auto err = res.err; // NOLINT (cppcoreguidelines-pro-type-union-access)
        if (!already_warned) {
            already_warned = true;
            const std::string errmsg = err_to_msg(&err, "Error resetting profile");
            std::cerr << "Could not drop profile:" << errmsg << std::endl;
        }
        ddog_Error_drop(&err);
        return false;
    }

    cur_profiler_stats.reset_state();
    return true;
}

void
Datadog::Profile::cleanup()
{
    // Drop the profile and release its resources
    ddog_prof_Profile_drop(&cur_profile);
    if (heap_enabled) {
        const std::lock_guard<std::mutex> lock(heap_profile_mtx);
        ddog_prof_Profile_drop(&heap_profile);
    }
}

void
Datadog::Profile::setup_samplers()
{
    // TODO propagate error if no valid samplers are defined
    samplers.clear();
    auto add_sampler = [this](ddog_prof_SampleType sample_type) {
        const size_t idx = this->samplers.size();
        this->samplers.push_back(sample_type);
        return idx;
    };

    // Check which samplers were enabled by the user
    if (0U != (type_mask & SampleType::CPU)) {
        val_idx.cpu_time = add_sampler(DDOG_PROF_SAMPLE_TYPE_CPU_TIME);
        val_idx.cpu_count = add_sampler(DDOG_PROF_SAMPLE_TYPE_CPU_SAMPLES);
    }
    if (0U != (type_mask & SampleType::Wall)) {
        val_idx.wall_time = add_sampler(DDOG_PROF_SAMPLE_TYPE_WALL_TIME);
        val_idx.wall_count = add_sampler(DDOG_PROF_SAMPLE_TYPE_WALL_SAMPLES);
    }
    if (0U != (type_mask & SampleType::Exception)) {
        val_idx.exception_count = add_sampler(DDOG_PROF_SAMPLE_TYPE_EXCEPTION_SAMPLES);
    }
    if (0U != (type_mask & SampleType::LockAcquire)) {
        val_idx.lock_acquire_time = add_sampler(DDOG_PROF_SAMPLE_TYPE_LOCK_ACQUIRE_WAIT);
        val_idx.lock_acquire_count = add_sampler(DDOG_PROF_SAMPLE_TYPE_LOCK_ACQUIRE);
    }
    if (0U != (type_mask & SampleType::LockRelease)) {
        val_idx.lock_release_time = add_sampler(DDOG_PROF_SAMPLE_TYPE_LOCK_RELEASE_HOLD);
        val_idx.lock_release_count = add_sampler(DDOG_PROF_SAMPLE_TYPE_LOCK_RELEASE);
    }
    if (0U != (type_mask & SampleType::Allocation)) {
        val_idx.alloc_space = add_sampler(DDOG_PROF_SAMPLE_TYPE_ALLOC_SPACE);
        val_idx.alloc_count = add_sampler(DDOG_PROF_SAMPLE_TYPE_ALLOC_SAMPLES);
    }
    if (0U != (type_mask & SampleType::Heap)) {
        val_idx.heap_space = add_sampler(DDOG_PROF_SAMPLE_TYPE_HEAP_SPACE);
        val_idx.heap_count = add_sampler(DDOG_PROF_SAMPLE_TYPE_HEAP_LIVE_SAMPLES);
    }
    if (0U != (type_mask & SampleType::GPUTime)) {
        val_idx.gpu_time = add_sampler(DDOG_PROF_SAMPLE_TYPE_GPU_TIME);
        val_idx.gpu_count = add_sampler(DDOG_PROF_SAMPLE_TYPE_GPU_SAMPLES);
    }
    if (0U != (type_mask & SampleType::GPUMemory)) {
        // In the backend the unit is called 'gpu-space', but maybe for consistency
        // it should be gpu-alloc-space
        // gpu-alloc-samples may be unused, but it's passed along for scaling purposes
        val_idx.gpu_alloc_space = add_sampler(DDOG_PROF_SAMPLE_TYPE_GPU_SPACE);
        val_idx.gpu_alloc_count = add_sampler(DDOG_PROF_SAMPLE_TYPE_GPU_ALLOC_SAMPLES);
    }
    if (0U != (type_mask & SampleType::GPUFlops)) {
        // Technically "FLOPS" is a unit, but we call it a 'count' because no
        // other profiler uses it as a unit.
        val_idx.gpu_flops = add_sampler(DDOG_PROF_SAMPLE_TYPE_GPU_FLOPS);
        val_idx.gpu_flops_samples = add_sampler(DDOG_PROF_SAMPLE_TYPE_GPU_FLOPS_SAMPLES);
    }

    // Whatever the first sampler happens to be is the default "period" for the profile
    // The value of 1 is a pointless default.
    if (!samplers.empty()) {
        default_period = { .sample_type = samplers[0], .value = 1 };
    }
}

size_t
Datadog::Profile::get_sample_type_length()
{
    return samplers.size();
}

Datadog::ProfileBorrow
Datadog::Profile::borrow()
{
    return ProfileBorrow(*this);
}

ddog_prof_Profile&
Datadog::Profile::profile_borrow_internal()
{
    // Note: Caller is responsible for ensuring profile_release() is called
    profile_mtx.lock();
    return cur_profile;
}

void
Datadog::Profile::profile_release()
{
    profile_mtx.unlock();
}

void
Datadog::Profile::one_time_init(SampleType type, unsigned int _max_nframes)
{
    std::call_once(init_once, [this, type, _max_nframes]() { one_time_init_impl(type, _max_nframes); });
}

void
Datadog::Profile::one_time_init_impl(SampleType type, unsigned int _max_nframes)
{
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety

    // nframes
    max_nframes = _max_nframes;

    // Set the type mask
    const unsigned int mask_as_int = type & SampleType::All;
    if (mask_as_int == 0) {
        // This can't happen in contemporary dd-trace-py, but we need better handling around this case
        if (!already_warned) {
            already_warned = true;
            std::cerr << "No valid sample types were enabled" << std::endl;
        }
        return;
    }
    type_mask = static_cast<SampleType>(mask_as_int);

    // Setup the samplers
    setup_samplers();

    // We need to initialize the profiles
    const ddog_prof_Slice_SampleType sample_types = { .ptr = samplers.data(), .len = samplers.size() };
    if (!make_profile(sample_types, &default_period, cur_profile)) {
        if (!already_warned) {
            already_warned = true;
            std::cerr << "Error initializing cur_profile" << std::endl;
        }
    }

    // When heap sampling is enabled, create the persistent heap profile. It
    // uses the same sample-type layout as cur_profile so a Sample built for one
    // can be applied to the other unchanged.
    if (0U != (type_mask & SampleType::Heap)) {
        if (make_profile(sample_types, &default_period, heap_profile)) {
            heap_enabled = true;
        } else if (!already_warned) {
            already_warned = true;
            std::cerr << "Error initializing heap_profile" << std::endl;
        }
    }
}

const Datadog::ValueIndex&
Datadog::Profile::val()
{
    return val_idx;
}

bool
Datadog::Profile::collect(const ddog_prof_Sample2& sample, int64_t endtime_ns)
{
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    const std::lock_guard<std::mutex> lock(profile_mtx);
    auto res = ddog_prof_Profile_add2(&cur_profile, sample, endtime_ns);
    if (res.flags) { // NOLINT (cppcoreguidelines-pro-type-union-access)
        if (!already_warned) {
            already_warned = true;
            const std::string errmsg = std::string(res.err);
            std::cerr << errmsg << std::endl;
        }
        return false;
    }
    return true;
}

Datadog::HeapApplyResult
Datadog::Profile::heap_apply_batch(const ddog_prof_Sample2* adds,
                                   size_t n_adds,
                                   const ddog_prof_Sample2* subs,
                                   size_t n_subs)
{
    static bool add_already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    static bool sub_already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    if (!heap_enabled) {
        // No persistent profile to keep consistent. Report success so the
        // caller drains its buffers instead of retaining them forever.
        return { true, true };
    }

    const std::lock_guard<std::mutex> lock(heap_profile_mtx);

    HeapApplyResult result{ true, true };

    // Mark the heap profile as carrying data so uploads attach the snapshot.
    // Sticky: once set it stays set for the lifetime of this profile so the
    // persistent live set keeps being uploaded even on intervals with no churn.
    if (n_adds > 0 || n_subs > 0) {
        heap_populated = true;
    }

    if (n_adds > 0) {
        const ddog_prof_Slice_Sample2 add_slice = { .ptr = adds, .len = n_adds };
        // Untimestamped (aggregated) samples: the persistent heap profile
        // relies on aggregation + decrement, which timestamped samples do not
        // support, so we always pass 0 for the timestamp.
        auto res = ddog_prof_Profile_add2_batch(&heap_profile, add_slice, 0);
        if (res.err != nullptr) { // NOLINT (cppcoreguidelines-pro-type-union-access)
            if (!add_already_warned) {
                add_already_warned = true;
                std::cerr << "Error adding heap samples: " << std::string(res.err) << std::endl;
            }
            result.adds_ok = false;
        }
        ddog_prof_Status_drop(&res);
    }

    if (n_subs > 0) {
        const ddog_prof_Slice_Sample2 sub_slice = { .ptr = subs, .len = n_subs };
        auto res = ddog_prof_Profile_sub2_batch(&heap_profile, sub_slice);
        if (res.err != nullptr) { // NOLINT (cppcoreguidelines-pro-type-union-access)
            if (!sub_already_warned) {
                sub_already_warned = true;
                std::cerr << "Error subtracting heap samples: " << std::string(res.err) << std::endl;
            }
            result.subs_ok = false;
        }
        ddog_prof_Status_drop(&res);
    }

    return result;
}

ddog_prof_Profile_SerializeResult
Datadog::Profile::heap_serialize_snapshot()
{
    if (!heap_enabled) {
        // Callers must check heap_is_enabled() first; this is just a defensive
        // fallback so the union is in a well-defined (ERR) state.
        ddog_prof_Profile_SerializeResult res{};
        res.tag = DDOG_PROF_PROFILE_SERIALIZE_RESULT_ERR;
        return res;
    }

    const std::lock_guard<std::mutex> lock(heap_profile_mtx);
    // Non-destructive: leaves heap_profile intact so it keeps accumulating.
    return ddog_prof_Profile_serialize_snapshot(&heap_profile, nullptr, nullptr);
}

bool
Datadog::Profile::reset_heap_profile()
{
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    if (!heap_enabled) {
        return false;
    }

    const std::lock_guard<std::mutex> lock(heap_profile_mtx);
    auto res = ddog_prof_Profile_reset(&heap_profile);
    if (!res.ok) {          // NOLINT (cppcoreguidelines-pro-type-union-access)
        auto err = res.err; // NOLINT (cppcoreguidelines-pro-type-union-access)
        if (!already_warned) {
            already_warned = true;
            const std::string errmsg = err_to_msg(&err, "Error resetting heap profile");
            std::cerr << "Could not reset heap profile: " << errmsg << std::endl;
        }
        ddog_Error_drop(&err);
        return false;
    }
    // The live set is now empty; don't attach a snapshot again until new data
    // is applied.
    heap_populated = false;
    return true;
}

void
Datadog::Profile::prefork()
{
    // Lock the profile mutex before fork to ensure the sampling thread is not
    // mid-allocation inside ddog_prof_Profile_add2 when the fork happens.
    // If the sampling thread is currently inside collect(), this will block
    // until it finishes, guaranteeing the IndexSet<StackTrace> is in a
    // fully-consistent state before the child calls ddog_prof_Profile_drop().
    profile_mtx.lock();

    // Same reasoning for the persistent heap profile: a concurrent (off-GIL)
    // heap_serialize_snapshot or a heap_apply_batch must finish before we fork,
    // so the child can safely drop and recreate it.
    if (heap_enabled) {
        heap_profile_mtx.lock();
    }
}

void
Datadog::Profile::postfork_parent()
{
    if (heap_enabled) {
        heap_profile_mtx.unlock();
    }
    profile_mtx.unlock();
}

void
Datadog::Profile::postfork_child()
{
    // Reset the profiler stats to clear any samples collected in the parent process
    cur_profiler_stats.reset_state();

    // Drop the old profile - it references the old (now-released) dictionary
    ddog_prof_Profile_drop(&cur_profile);

    // Create a new profile with the new dictionary
    const ddog_prof_Slice_SampleType sample_types = { .ptr = samplers.data(), .len = samplers.size() };
    if (!make_profile(sample_types, &default_period, cur_profile)) {
        std::cerr << "Error re-initializing profile after fork" << std::endl;
    }

    // Recreate the persistent heap profile with the new dictionary. This also
    // discards any allocations accumulated in the parent, which is correct: the
    // child starts heap tracking fresh (the collector clears its pending buffer
    // in heap_tracker_t::postfork_child()). heap_profile_mtx was locked by
    // prefork; we hold it here (single-threaded child) and unlock below.
    if (heap_enabled) {
        ddog_prof_Profile_drop(&heap_profile);
        // The child starts heap tracking fresh, so the recreated profile is
        // empty until the collector applies its first batch.
        heap_populated = false;
        if (!make_profile(sample_types, &default_period, heap_profile)) {
            heap_enabled = false;
            std::cerr << "Error re-initializing heap profile after fork" << std::endl;
        }
        heap_profile_mtx.unlock();
    }

    // Unlock profile_mtx, which was locked by prefork.
    profile_mtx.unlock();
}
