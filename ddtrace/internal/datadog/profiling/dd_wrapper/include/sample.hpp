#pragma once

#include "libdatadog_helpers.hpp"
#include "profile.hpp"
#include "types.hpp"

#include <string>
#include <string_view>
#include <vector>

extern "C"
{
#include "datadog/profiling.h"
}

namespace Datadog {

namespace internal {

// StringArena holds copies of strings we need while building samples.
// StringArena is intended to amortize allocations, so that in the common
// case we can just do a memcpy for each string we want to copy rather than
// a new allocation for each string.
//
// We need to make copies right now because we don't have strong guarantees
// that the strings we get (from Python, Cython, C++, etc) are alive the
// whole time we build samples.
struct StringArena
{
    // Default size, in bytes, of each Chunk. The value is a power of 2 (nice to
    // allocate) that is bigger than any actual sample string size seen over a
    // random selection of a few hundred Python profiles at Datadog. So ideally
    // we only need one chunk, which we can reuse between samples
    static constexpr size_t DEFAULT_SIZE = 16 * 1024;
    // Strings are backed by fixed-size Chunks. The Chunks can't grow, or
    // they'll move and invalidate pointers into the arena. At the same time,
    // they must be dynamically sized at creation because we get arbitrary
    // user-provided strings.
    using Chunk = std::vector<char>;
    // We keep the Chunks for this arena in a vector so we can track them, and
    // free them when the StringArena is deallocated.
    std::vector<Chunk> chunks;

    StringArena();
    // Clear the backing data of the arena, except for a smaller initial segment.
    // Views returned by insert are invalid after this call.
    void reset();
    // Copies the contents of s into the arena and returns a view of the copy in
    // the arena. The returned view is valid until the next call to reset, or
    // until the arena is destroyed.
    std::string_view insert(std::string_view s);
};

} // namespace internal

class SampleManager; // friend

class Sample
{
  private:
    static inline Profile profile_state{}; // TODO pointer to global state?
    unsigned int max_nframes;
    SampleType type_mask;
    std::string errmsg;

    // Timeline support works by endowing each sample with a timestamp. Collection of this data this data is cheap, but
    // due to the underlying pprof format, timeline support increases the sample cardinality. Rather than switching
    // around the frontend code too much, we push enablement down to whether or not timestamps get added to samples (a
    // 0 value suppresses the tag). However, Sample objects are short-lived, so we make the flag static.
    static inline bool timeline_enabled = false;

    // Keeps temporary buffer of frames in the stack
    std::vector<ddog_prof_Location> locations;
    size_t dropped_frames = 0;
    uint64_t samples = 0;

    // Storage for labels
    std::vector<ddog_prof_Label> labels{};

    // Storage for values
    std::vector<int64_t> values = {};

    // Additional metadata
    int64_t endtime_ns = 0; // end of the event

    // Backing memory for string copies
    internal::StringArena string_storage{};

  public:
    // Helpers
    bool push_label(ExportLabelKey key, std::string_view val);
    bool push_label(ExportLabelKey key, int64_t val);
    void push_frame_impl(std::string_view name, std::string_view filename, uint64_t address, int64_t line);
    void clear_buffers();

    // Add values
    bool push_walltime(int64_t walltime, int64_t count);
    bool push_cputime(int64_t cputime, int64_t count);
    bool push_acquire(int64_t acquire_time, int64_t count);
    bool push_release(int64_t lock_time, int64_t count);
    bool push_alloc(int64_t size, int64_t count);
    bool push_heap(int64_t size);
    bool push_gpu_gputime(int64_t time, int64_t count);
    bool push_gpu_memory(int64_t size, int64_t count);
    bool push_gpu_flops(int64_t flops, int64_t count);

    // Adds metadata to sample
    bool push_lock_name(std::string_view lock_name);
    bool push_threadinfo(int64_t thread_id, int64_t thread_native_id, std::string_view thread_name);
    bool push_task_id(int64_t task_id);
    bool push_task_name(std::string_view task_name);
    bool push_span_id(uint64_t span_id);
    bool push_local_root_span_id(uint64_t local_root_span_id);
    bool push_trace_type(std::string_view trace_type);
    bool push_exceptioninfo(std::string_view exception_type, int64_t count);
    bool push_class_name(std::string_view class_name);
    bool push_monotonic_ns(int64_t monotonic_ns);
    bool push_absolute_ns(int64_t timestamp_ns);

    // Interacts with static Sample state
    bool is_timeline_enabled() const;
    static void set_timeline(bool enabled);

    // Pytorch GPU metadata
    bool push_gpu_device_name(std::string_view device_name);

    // Assumes frames are pushed in leaf-order
    void push_frame(std::string_view name,     // for ddog_prof_Function
                    std::string_view filename, // for ddog_prof_Function
                    uint64_t address,          // for ddog_prof_Location
                    int64_t line               // for ddog_prof_Location
    );

    // Flushes the current buffer, clearing it
    bool flush_sample(bool reverse_locations = false);

    static ddog_prof_Profile& profile_borrow();
    static void profile_release();
    static void profile_clear_state();
    Sample(SampleType _type_mask, unsigned int _max_nframes);

    // friend class SampleManager;
    friend class SampleManager;
};

} // namespace Datadog
