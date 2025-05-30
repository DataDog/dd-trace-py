#include "profile.hpp"

#include "libdatadog_helpers.hpp"

#include <functional>
#include <iostream>

#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>
#ifdef _WIN32
#include <io.h>
#else
#include <unistd.h>
#endif

// Inline helpers
namespace {

inline bool
make_profile(const ddog_prof_Slice_ValueType& sample_types,
             const struct ddog_prof_Period* period,
             ddog_prof_Profile& profile)
{
    // Private helper function for creating a ddog_prof_Profile from arguments
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    ddog_prof_Profile_NewResult res = ddog_prof_Profile_new(sample_types, period);
    if (res.tag != DDOG_PROF_PROFILE_NEW_RESULT_OK) { // NOLINT (cppcoreguidelines-pro-type-union-access)
        auto err = res.err;                           // NOLINT (cppcoreguidelines-pro-type-union-access)
        if (!already_warned) {
            already_warned = true;
            const std::string errmsg = Datadog::err_to_msg(&err, "Error initializing profile");
            std::cerr << errmsg << std::endl;
        }
        ddog_Error_drop(&err);
        return false;
    }
    profile = res.ok; // NOLINT (cppcoreguidelines-pro-type-union-access)
    return true;
}

}

bool
Datadog::Profile::cycle_buffers()
{
    const std::lock_guard<std::mutex> lock(profile_mtx);
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety

    std::swap(last_profile, cur_profile);

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
    return true;
}

void
Datadog::Profile::setup_samplers()
{
    // TODO propagate error if no valid samplers are defined
    samplers.clear();
    auto get_value_idx = [this](std::string_view value, std::string_view unit) {
        const size_t idx = this->samplers.size();
        this->samplers.push_back({ to_slice(value), to_slice(unit) });
        return idx;
    };

    // Check which samplers were enabled by the user
    if (0U != (type_mask & SampleType::CPU)) {
        val_idx.cpu_time = get_value_idx("cpu-time", "nanoseconds");
        val_idx.cpu_count = get_value_idx("cpu-samples", "count");
    }
    if (0U != (type_mask & SampleType::Wall)) {
        val_idx.wall_time = get_value_idx("wall-time", "nanoseconds");
        val_idx.wall_count = get_value_idx("wall-samples", "count");
    }
    if (0U != (type_mask & SampleType::Exception)) {
        val_idx.exception_count = get_value_idx("exception-samples", "count");
    }
    if (0U != (type_mask & SampleType::LockAcquire)) {
        val_idx.lock_acquire_time = get_value_idx("lock-acquire-wait", "nanoseconds");
        val_idx.lock_acquire_count = get_value_idx("lock-acquire", "count");
    }
    if (0U != (type_mask & SampleType::LockRelease)) {
        val_idx.lock_release_time = get_value_idx("lock-release-hold", "nanoseconds");
        val_idx.lock_release_count = get_value_idx("lock-release", "count");
    }
    if (0U != (type_mask & SampleType::Allocation)) {
        val_idx.alloc_space = get_value_idx("alloc-space", "bytes");
        val_idx.alloc_count = get_value_idx("alloc-samples", "count");
    }
    if (0U != (type_mask & SampleType::Heap)) {
        val_idx.heap_space = get_value_idx("heap-space", "bytes");
    }
    if (0U != (type_mask & SampleType::GPUTime)) {
        val_idx.gpu_time = get_value_idx("gpu-time", "nanoseconds");
        val_idx.gpu_count = get_value_idx("gpu-samples", "count");
    }
    if (0U != (type_mask & SampleType::GPUMemory)) {
        // In the backend the unit is called 'gpu-space', but maybe for consistency
        // it should be gpu-alloc-space
        // gpu-alloc-samples may be unused, but it's passed along for scaling purposes
        val_idx.gpu_alloc_space = get_value_idx("gpu-space", "bytes");
        val_idx.gpu_alloc_count = get_value_idx("gpu-alloc-samples", "count");
    }
    if (0U != (type_mask & SampleType::GPUFlops)) {
        // Technically "FLOPS" is a unit, but we call it a 'count' because no
        // other profiler uses it as a unit.
        val_idx.gpu_flops = get_value_idx("gpu-flops", "count");
        val_idx.gpu_flops_samples = get_value_idx("gpu-flops-samples", "count");
    }

    // Whatever the first sampler happens to be is the default "period" for the profile
    // The value of 1 is a pointless default.
    if (!samplers.empty()) {
        default_period = { .type_ = samplers[0], .value = 1 };
    }
}

size_t
Datadog::Profile::get_sample_type_length()
{
    return samplers.size();
}

ddog_prof_Profile&
Datadog::Profile::profile_borrow()
{
    // We could wrap this in an object for better RAII, but since this
    // sequence is only used in a single place, we'll hold off on that sidequest.
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
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    // In contemporary dd-trace-py, it is expected that the initialization path is in
    // a single thread, and done only once.
    // However, it doesn't cost us much to keep this initialization tight.
    if (!first_time.load()) {
        return;
    }

    // Threads need to serialize at this point
    const std::lock_guard<std::mutex> lock(profile_mtx);

    // nframes
    max_nframes = _max_nframes;

    // Set the type mask
    const unsigned int mask_as_int = type & SampleType::All;
    if (mask_as_int == 0) {
        // This can't happen in contemporary dd-trace-py, but we need better handling around this case
        // TODO fix this
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
    const ddog_prof_Slice_ValueType sample_types = { .ptr = samplers.data(), .len = samplers.size() };
    if (!make_profile(sample_types, &default_period, cur_profile)) {
        if (!already_warned) {
            already_warned = true;
            std::cerr << "Error initializing profile" << std::endl;
        }
        return;
    }
    if (!make_profile(sample_types, &default_period, last_profile)) {
        if (!already_warned) {
            already_warned = true;
            std::cerr << "Error initializing last profile" << std::endl;
        }
        return;
    }

    // We're done. Don't do this again.
    first_time.store(false);
}

const Datadog::ValueIndex&
Datadog::Profile::val()
{
    return val_idx;
}

bool
Datadog::Profile::collect(const ddog_prof_Sample& sample, int64_t endtime_ns)
{
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    const std::lock_guard<std::mutex> lock(profile_mtx);
    auto res = ddog_prof_Profile_add(&cur_profile, sample, endtime_ns);
    if (!res.ok) {          // NOLINT (cppcoreguidelines-pro-type-union-access)
        auto err = res.err; // NOLINT (cppcoreguidelines-pro-type-union-access)
        if (!already_warned) {
            already_warned = true;
            const std::string errmsg = err_to_msg(&err, "Error adding sample to profile");
            std::cerr << errmsg << std::endl;
        }
        ddog_Error_drop(&err);
        return false;
    }
    return true;
}

void
Datadog::Profile::postfork_child()
{
    new (&profile_mtx) std::mutex();
    cycle_buffers();
}
