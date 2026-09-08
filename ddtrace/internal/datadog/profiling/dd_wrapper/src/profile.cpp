#include "profile.hpp"

#include "profile_borrow.hpp"
#include "profiler_state.hpp"
#include "profiler_stats.hpp"

#include <iostream>
#include <utility>

#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

// Inline helpers
namespace {

inline bool
make_profile(const std::vector<Datadog::ddprof::SampleType>& sample_types,
             const Datadog::ddprof::Period& period,
             std::optional<rust::Box<Datadog::ddprof::Profile>>& profile)
{
    // Private helper function for creating a CXX Profile from arguments

    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    auto* dict = Datadog::ProfilerState::get().get_profiles_dictionary();
    if (dict == nullptr) {
        return false;
    }

    try {
        rust::Vec<Datadog::ddprof::SampleType> cxx_sample_types;
        for (const auto sample_type : sample_types) {
            cxx_sample_types.push_back(sample_type);
        }
        profile.emplace(Datadog::ddprof::Profile::create_with_dictionary(std::move(cxx_sample_types), period, *dict));
    } catch (const std::exception& err) {
        if (!already_warned) {
            already_warned = true;
            std::cerr << "Error creating CXX profile: " << err.what() << std::endl;
        }
        return false;
    }
    return true;
}

} // namespace

bool
Datadog::Profile::reset_profile()
{
    const std::lock_guard<std::mutex> lock(profile_mtx);
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety

    if (!cur_profile.has_value()) {
        return false;
    }

    try {
        cur_profile.value()->reset();
    } catch (const std::exception& err) {
        if (!already_warned) {
            already_warned = true;
            std::cerr << "Could not reset CXX profile: " << err.what() << std::endl;
        }
        return false;
    }

    cur_profiler_stats.reset_state();
    return true;
}

void
Datadog::Profile::cleanup()
{
    // Drop the profile and release its resources
    cur_profile.reset();
}

void
Datadog::Profile::setup_samplers()
{
    // TODO propagate error if no valid samplers are defined
    samplers.clear();
    auto add_sampler = [this](ddprof::SampleType sample_type) {
        const size_t idx = this->samplers.size();
        this->samplers.push_back(sample_type);
        return idx;
    };

    // Check which samplers were enabled by the user
    if (0U != (type_mask & SampleType::CPU)) {
        val_idx.cpu_time = add_sampler(ddprof::SampleType::CpuTime);
        val_idx.cpu_count = add_sampler(ddprof::SampleType::CpuSamples);
    }
    if (0U != (type_mask & SampleType::Wall)) {
        val_idx.wall_time = add_sampler(ddprof::SampleType::WallTime);
        val_idx.wall_count = add_sampler(ddprof::SampleType::WallSamples);
    }
    if (0U != (type_mask & SampleType::Exception)) {
        val_idx.exception_count = add_sampler(ddprof::SampleType::ExceptionSamples);
    }
    if (0U != (type_mask & SampleType::LockAcquire)) {
        val_idx.lock_acquire_time = add_sampler(ddprof::SampleType::LockAcquireWait);
        val_idx.lock_acquire_count = add_sampler(ddprof::SampleType::LockAcquire);
    }
    if (0U != (type_mask & SampleType::LockRelease)) {
        val_idx.lock_release_time = add_sampler(ddprof::SampleType::LockReleaseHold);
        val_idx.lock_release_count = add_sampler(ddprof::SampleType::LockRelease);
    }
    if (0U != (type_mask & SampleType::Allocation)) {
        val_idx.alloc_space = add_sampler(ddprof::SampleType::AllocSpace);
        val_idx.alloc_count = add_sampler(ddprof::SampleType::AllocSamples);
    }
    if (0U != (type_mask & SampleType::Heap)) {
        val_idx.heap_space = add_sampler(ddprof::SampleType::HeapSpace);
        val_idx.heap_count = add_sampler(ddprof::SampleType::HeapLiveSamples);
    }
    if (0U != (type_mask & SampleType::GPUTime)) {
        val_idx.gpu_time = add_sampler(ddprof::SampleType::GpuTime);
        val_idx.gpu_count = add_sampler(ddprof::SampleType::GpuSamples);
    }
    if (0U != (type_mask & SampleType::GPUMemory)) {
        // In the backend the unit is called 'gpu-space', but maybe for consistency
        // it should be gpu-alloc-space
        // gpu-alloc-samples may be unused, but it's passed along for scaling purposes
        val_idx.gpu_alloc_space = add_sampler(ddprof::SampleType::GpuSpace);
        val_idx.gpu_alloc_count = add_sampler(ddprof::SampleType::GpuAllocSamples);
    }
    if (0U != (type_mask & SampleType::GPUFlops)) {
        // Technically "FLOPS" is a unit, but we call it a 'count' because no
        // other profiler uses it as a unit.
        val_idx.gpu_flops = add_sampler(ddprof::SampleType::GpuFlops);
        val_idx.gpu_flops_samples = add_sampler(ddprof::SampleType::GpuFlopsSamples);
    }

    // Whatever the first sampler happens to be is the default "period" for the profile
    // The value of 1 is a pointless default.
    if (!samplers.empty()) {
        default_period = { .value_type = samplers[0], .value = 1 };
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

Datadog::ddprof::Profile&
Datadog::Profile::profile_borrow_internal()
{
    // Note: Caller is responsible for ensuring profile_release() is called
    profile_mtx.lock();
    return *cur_profile.value();
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
    if (!make_profile(samplers, default_period, cur_profile)) {
        if (!already_warned) {
            already_warned = true;
            std::cerr << "Error initializing cur_profile" << std::endl;
        }
    }
}

const Datadog::ValueIndex&
Datadog::Profile::val()
{
    return val_idx;
}

std::vector<std::uint8_t>
Datadog::Profile::serialize_to_vec()
{
    const std::lock_guard<std::mutex> lock(profile_mtx);
    auto encoded = cur_profile.value()->serialize_to_vec();
    return std::vector<std::uint8_t>(encoded.data(), encoded.data() + encoded.size());
}

bool
Datadog::Profile::add_endpoint(std::int64_t local_root_span_id, std::string_view endpoint)
{
    try {
        cur_profile.value()->add_endpoint(static_cast<std::uint64_t>(local_root_span_id),
                                          rust::Str(endpoint.data(), endpoint.size()));
        return true;
    } catch (const std::exception& err) {
        std::cerr << "CXX add_endpoint failed: " << err.what() << std::endl;
        return false;
    }
}

bool
Datadog::Profile::add_endpoint_count(std::string_view endpoint, std::int64_t value)
{
    try {
        cur_profile.value()->add_endpoint_count(rust::Str(endpoint.data(), endpoint.size()), value);
        return true;
    } catch (const std::exception& err) {
        std::cerr << "CXX add_endpoint_count failed: " << err.what() << std::endl;
        return false;
    }
}

bool
Datadog::Profile::collect(const ddprof::Sample2& sample, int64_t endtime_ns)
{
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    const std::lock_guard<std::mutex> lock(profile_mtx);
    try {
        cur_profile.value()->add_sample2(sample, endtime_ns);
    } catch (const std::exception& err) {
        if (!already_warned) {
            already_warned = true;
            std::cerr << "CXX add_sample2 failed: " << err.what() << std::endl;
        }
        return false;
    }
    return true;
}

void
Datadog::Profile::prefork()
{
    // Lock the profile mutex before fork to ensure the sampling thread is not
    // mid-allocation inside add_sample2 when the fork happens. If the sampling
    // thread is currently inside collect(), this will block until it finishes,
    // guaranteeing the IndexSet<StackTrace> is in a fully-consistent state
    // before the child drops the profile.
    profile_mtx.lock();
}

void
Datadog::Profile::postfork_parent()
{
    profile_mtx.unlock();
}

void
Datadog::Profile::postfork_child()
{
    // Reset the profiler stats to clear any samples collected in the parent process
    cur_profiler_stats.reset_state();

    // Drop the old profile - it references the old (now-released) dictionary
    cur_profile.reset();

    // Create a new profile with the new dictionary
    if (!make_profile(samplers, default_period, cur_profile)) {
        std::cerr << "Error re-initializing profile after fork" << std::endl;
    }

    // Unlock profile_mtx, which was locked by prefork.
    profile_mtx.unlock();
}
