#include "profile.hpp"
#include "libdatadog_helpers.hpp"

#include <functional>
#include <iostream>

#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

using namespace Datadog;

void
Profile::reset()
{
    // Drop the profiles
    if (cur_profile.inner != nullptr) {
        ddog_prof_Profile_drop(&cur_profile);
    }
    if (last_profile.inner != nullptr) {
        ddog_prof_Profile_drop(&last_profile);
    }

    // non-RAII heap storage has been dropped, state has been reinitialized
    // we can pretend it's the first time all over again
    first_time.store(true);
}

bool
Profile::cycle_buffers()
{
    std::lock_guard<std::mutex> lock(profile_mtx);
    std::swap(last_profile, cur_profile);

    // Clear the profile before using it
    auto res = ddog_prof_Profile_reset(&cur_profile, nullptr);
    if (!res.ok) {
        std::string errmsg = err_to_msg(&res.err, "Error resetting profile");
        ddog_Error_drop(&res.err);
        std::cout << "Could not drop profile:" << errmsg << std::endl;
        return false;
    }
    return true;
}

void
Profile::entrypoint_check()
{
    if (dirty.load()) {
        std::lock_guard<std::mutex> lock(profile_mtx);
        cycle_buffers();
        dirty.store(false);
    }
}

void
Profile::setup_samplers()
{
    // TODO propagate error if no valid samplers are defined
    samplers.clear();
    auto get_value_idx = [this](std::string_view value, std::string_view unit) {
        size_t idx = this->samplers.size();
        this->samplers.push_back({ to_slice(value), to_slice(unit) });
        return idx;
    };

    // Check which samplers were enabled by the user
    if (type_mask & SampleType::CPU) {
        val_idx.cpu_time = get_value_idx("cpu-time", "nanoseconds");
        val_idx.cpu_count = get_value_idx("cpu-samples", "count");
    }
    if (type_mask & SampleType::Wall) {
        val_idx.wall_time = get_value_idx("wall-time", "nanoseconds");
        val_idx.wall_count = get_value_idx("wall-samples", "count");
    }
    if (type_mask & SampleType::Exception) {
        val_idx.exception_count = get_value_idx("exception-samples", "count");
    }
    if (type_mask & SampleType::LockAcquire) {
        val_idx.lock_acquire_time = get_value_idx("lock-acquire-wait", "nanoseconds");
        val_idx.lock_acquire_count = get_value_idx("lock-acquire", "count");
    }
    if (type_mask & SampleType::LockRelease) {
        val_idx.lock_release_time = get_value_idx("lock-release-hold", "nanoseconds");
        val_idx.lock_release_count = get_value_idx("lock-release", "count");
    }
    if (type_mask & SampleType::Allocation) {
        val_idx.alloc_space = get_value_idx("alloc-space", "bytes");
        val_idx.alloc_count = get_value_idx("alloc-samples", "count");
    }
    if (type_mask & SampleType::Heap) {
        val_idx.heap_space = get_value_idx("heap-space", "bytes");
    }

    // Whatever the first sampler happens to be is the default "period" for the profile
    // The value of 1 is a pointless default.
    if (samplers.size() > 0) {
        default_period = { .type_ = samplers[0], .value = 1 };
    }
}

size_t
Profile::get_sample_type_length()
{
    return samplers.size();
}

ddog_prof_Profile&
Profile::get_current_profile()
{
    return cur_profile;
}

unsigned int
Profile::get_max_nframes()
{
    return max_nframes;
}

SampleType
Profile::get_type_mask()
{
    return type_mask;
}

static inline bool
make_profile(const ddog_prof_Slice_ValueType& sample_types,
             const struct ddog_prof_Period* period,
             ddog_prof_Profile& profile)
{
    ddog_prof_Profile_NewResult res = ddog_prof_Profile_new(sample_types, period, nullptr);
    if (res.tag != DDOG_PROF_PROFILE_NEW_RESULT_OK) {
        std::string errmsg = err_to_msg(&res.err, "Error initializing profile");
        std::cerr << errmsg << std::endl;
        ddog_Error_drop(&res.err);
        return false;
    }
    profile = res.ok;
    return true;
}

void
Profile::one_time_init(SampleType type, unsigned int _max_nframes)
{
    // In contemporary dd-trace-py, it is expected that the initialization path is in
    // a single thread, and done only once.
    // However, it doesn't cost us much to keep this initialization tight.
    if (!first_time.load())
        return;

    // Threads need to serialize at this point
    std::lock_guard<std::mutex> lock(profile_mtx);

    // nframes
    max_nframes = _max_nframes;

    // Set the type mask
    unsigned int mask_as_int = type & SampleType::All;
    type_mask = static_cast<SampleType>(mask_as_int);

    // Setup the samplers
    setup_samplers();
    current_pid.store(getpid());

    // We need to initialize the profiles
    ddog_prof_Slice_ValueType sample_types = { .ptr = samplers.data(), .len = samplers.size() };
    if (!make_profile(sample_types, &default_period, cur_profile)) {
        std::cerr << "Error initializing top half of profile storage" << std::endl;
        return;
    }
    if (!make_profile(sample_types, &default_period, last_profile)) {
        std::cerr << "Error initializing bottom half of profile storage" << std::endl;
        return;
    }

    // We're done. Don't do this again.
    first_time.store(false);
}

std::string_view
Profile::insert_or_get(std::string_view sv)
{
    std::lock_guard<std::mutex> lock(string_table_mtx); // Serialize access

    auto it = strings.find(sv);
    if (it != strings.end()) {
        return *it;
    } else {
        string_storage.emplace_back(sv);
        strings.insert(string_storage.back());
        return string_storage.back();
    }
}

const ValueIndex&
Profile::val()
{
    return val_idx;
}

bool
Profile::collect(const ddog_prof_Sample& sample)
{
    // TODO this should propagate some kind of timestamp for timeline support
    std::lock_guard<std::mutex> lock(profile_mtx);
    auto res = ddog_prof_Profile_add(&cur_profile, sample, 0);
    if (!res.ok) {
        std::string errmsg = err_to_msg(&res.err, "Error adding sample to profile");
        ddog_Error_drop(&res.err);
        std::cerr << errmsg << std::endl;
        return false;
    }
    return true;
}

void
Profile::mark_dirty()
{
    dirty.store(true);
}
