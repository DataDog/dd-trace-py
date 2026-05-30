#include "profile.hpp"

#include "libdatadog_helpers.hpp"
#include "profile_borrow.hpp"
#include "profiler_state.hpp"
#include "profiler_stats.hpp"
#include "sample.hpp"

#include <algorithm>
#include <atomic>
#include <datadog/profiling.h>
#include <iostream>
#include <mutex>
#include <vector>

#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

namespace {

// One pending sample's worth of moved-out state from a Sample.
// Vectors keep their backing storage after std::move so ddog_prof_Sample2
// can reference .data()/.size() at flush time. The StringArena's chunks are
// std::vector<char>, also stable under std::move, so label string_views
// captured before the move stay valid.
struct PendingSample
{
    std::vector<ddog_prof_Location2> locations;
    std::vector<int64_t> values;
    std::vector<ddog_prof_Label2> labels;
    Datadog::internal::StringArena string_storage;
    int64_t endtime_ns{ 0 };
};

// Per-thread batch slot. Registers itself in the process-global slot
// registry at construction so flush_all_thread_batches() can drain every
// live thread without per-thread cooperation.
struct ThreadBatchSlot
{
    std::vector<PendingSample> pending;
    ThreadBatchSlot();
    ~ThreadBatchSlot();
};

std::mutex slot_registry_mtx;
std::vector<ThreadBatchSlot*> slot_registry;
std::atomic<bool> batching_enabled_flag{ true };

ThreadBatchSlot::ThreadBatchSlot()
{
    const std::lock_guard<std::mutex> lk(slot_registry_mtx);
    slot_registry.push_back(this);
}

ThreadBatchSlot::~ThreadBatchSlot()
{
    // Drop any unflushed samples on thread exit. flush_all_thread_batches()
    // is called at upload/fork, so the unflushed tail is at most one batch
    // window. Acceptable per the design.
    const std::lock_guard<std::mutex> lk(slot_registry_mtx);
    slot_registry.erase(std::remove(slot_registry.begin(), slot_registry.end(), this), slot_registry.end());
}

ThreadBatchSlot&
tls_slot()
{
    thread_local ThreadBatchSlot slot{};
    return slot;
}

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

void
Datadog::Profile::drain_tls_locked_()
{
    // profile_mtx must be held by caller.
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    auto& pending = tls_slot().pending;
    for (auto& ps : pending) {
        const ddog_prof_Sample2 sample = {
            .locations = { ps.locations.data(), ps.locations.size() },
            .values = { ps.values.data(), ps.values.size() },
            .labels = { ps.labels.data(), ps.labels.size() },
        };
        auto res = ddog_prof_Profile_add2(&cur_profile, sample, ps.endtime_ns);
        if (res.flags) { // NOLINT (cppcoreguidelines-pro-type-union-access)
            if (!already_warned) {
                already_warned = true;
                const std::string errmsg = std::string(res.err);
                std::cerr << errmsg << std::endl;
            }
        }
    }
    pending.clear();
}

bool
Datadog::Profile::buffered_collect(Sample& s)
{
    if (!batching_enabled_flag.load(std::memory_order_relaxed)) {
        // Sync path: skip the TLS batch, call Profile_add2 directly.
        // Used by microbench/kill-switch.
        const ddog_prof_Sample2 sample = {
            .locations = { s.locations.data(), s.locations.size() },
            .values = { s.values.data(), s.values.size() },
            .labels = { s.labels.data(), s.labels.size() },
        };
        return collect(sample, s.endtime_ns);
    }
    auto& slot = tls_slot();
    // Copy the small POD vectors (locations / values / labels combined are
    // usually well under a kilobyte). Only MOVE the StringArena, which owns
    // heap-allocated char chunks. Copying everything would lose the win of
    // batching; moving everything forces Sample to reallocate vectors on
    // every sample (its locations is reserved to max_nframes+1, ~65
    // entries). Partial-move keeps Sample's vector capacities intact across
    // samples while still avoiding the per-sample StringArena copy.
    slot.pending.push_back(PendingSample{
      s.locations,
      s.values,
      s.labels,
      std::move(s.string_storage),
      s.endtime_ns,
    });
    if (slot.pending.size() >= k_batch_threshold) {
        flush_thread_batch();
    }
    return true;
}

void
Datadog::Profile::set_batching_enabled(bool enabled)
{
    batching_enabled_flag.store(enabled, std::memory_order_relaxed);
}

bool
Datadog::Profile::batching_enabled()
{
    return batching_enabled_flag.load(std::memory_order_relaxed);
}

void
Datadog::Profile::flush_thread_batch()
{
    if (tls_slot().pending.empty()) {
        return;
    }
    const std::lock_guard<std::mutex> lock(profile_mtx);
    drain_tls_locked_();
}

void
Datadog::Profile::flush_all_thread_batches()
{
    // Take both locks in a fixed order (registry, then profile) to drain
    // every live thread's batch under one profile_mtx critical section.
    const std::lock_guard<std::mutex> reg_lk(slot_registry_mtx);
    const std::lock_guard<std::mutex> prof_lk(profile_mtx);
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    for (auto* slot : slot_registry) {
        for (auto& ps : slot->pending) {
            const ddog_prof_Sample2 sample = {
                .locations = { ps.locations.data(), ps.locations.size() },
                .values = { ps.values.data(), ps.values.size() },
                .labels = { ps.labels.data(), ps.labels.size() },
            };
            auto res = ddog_prof_Profile_add2(&cur_profile, sample, ps.endtime_ns);
            if (res.flags) { // NOLINT (cppcoreguidelines-pro-type-union-access)
                if (!already_warned) {
                    already_warned = true;
                    const std::string errmsg = std::string(res.err);
                    std::cerr << errmsg << std::endl;
                }
            }
        }
        slot->pending.clear();
    }
}

void
Datadog::Profile::prefork()
{
    // Drain every thread's pending batch first so the libdd Profile is
    // consistent before the child clones it. flush_all_thread_batches()
    // acquires both slot_registry_mtx and profile_mtx, then releases them.
    flush_all_thread_batches();

    // Lock the profile mutex before fork to ensure the sampling thread is not
    // mid-allocation inside ddog_prof_Profile_add2 when the fork happens.
    // Also lock slot_registry_mtx so the child wakes with both locks held in
    // a consistent state (it'll release them in postfork_child).
    slot_registry_mtx.lock();
    profile_mtx.lock();
}

void
Datadog::Profile::postfork_parent()
{
    profile_mtx.unlock();
    slot_registry_mtx.unlock();
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

    // After fork, only the calling thread is alive in the child. Other
    // threads' slot pointers in slot_registry are stale; drop them and
    // re-register the surviving thread's slot. Pending entries (if any)
    // reference function_ids in the parent's now-released dictionary, so
    // clear them too.
    auto* surviving = &tls_slot();
    surviving->pending.clear();
    slot_registry.clear();
    slot_registry.push_back(surviving);

    // Unlock both, in reverse order of prefork acquisition.
    profile_mtx.unlock();
    slot_registry_mtx.unlock();
}
