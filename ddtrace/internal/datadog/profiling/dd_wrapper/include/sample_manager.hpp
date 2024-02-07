#pragma once

#include "sample.hpp"
#include "types.hpp"

#include <array>
#include <atomic>
#include <optional>
#include <vector>

namespace Datadog {

// Tradeoff: either Invalid is 0 (nice: uninitialized state is invalid) or
//           it's >_Length (nice: can place real sample types in an array + index by enum)
// Choosing the latter for simplicity and because we own all downstream consumption for now
enum class SampleHandle
{
    Stack,
    Lock,
    Allocation,
    Heap,
    _Length,
    Invalid,
};

using SampleStorage = std::vector<Sample>;

class SampleManager
{
  private:
    static inline std::array<std::atomic<bool>, static_cast<size_t>(SampleHandle::_Length)> handler_state{};
    static inline std::optional<SampleStorage> storage{};
    static inline unsigned int max_nframes{ 64 };

    // Helpers
    static bool take_handler(SampleHandle handle);
    static void release_handler(SampleHandle handle);
    static inline SampleType type_mask{ SampleType::All };
    static inline void build_storage();

  public:
    // Configuration
    static void add_type(SampleType type);
    static void add_type(unsigned int type);
    static void set_max_nframes(unsigned int _max_nframes);

    // Tries to check out the requested handler, clearing its storage if successful
    static SampleHandle start_sample(SampleHandle requested);
    static SampleHandle start_sample(unsigned int requested);

    // Resets the state of the Profile object which is shared between Samples
    // NB this is probably only valuable in consolidating shut-down
    // operations and testing
    static void reset_profile();

    //-------------------------------------------------------------------------
    // Setup some variadic templates with perfect forwarding--this prevents the Manager from having to
    // overspecify the Sample API just to implement the proxy
    // This is verbose, but it's simple.
    template<typename... Args>
    static inline bool push_label(SampleHandle handle, Args&&... args)
    {
        if (handle == SampleHandle::Invalid) {
            return false;
        }
        return storage->at(static_cast<size_t>(handle)).push_label(std::forward<Args>(args)...);
    }

    template<typename... Args>
    static inline void push_frame_impl(SampleHandle handle, Args&&... args)
    {
        if (handle == SampleHandle::Invalid) {
            return;
        }
        storage->at(static_cast<size_t>(handle)).push_frame(std::forward<Args>(args)...);
    }

    template<typename... Args>
    static inline void clear_buffers(SampleHandle handle, Args&&... args)
    {
        if (handle == SampleHandle::Invalid) {
            return;
        }
        storage->at(static_cast<size_t>(handle)).clear_buffers(std::forward<Args>(args)...);
    }

    template<typename... Args>
    static inline bool push_walltime(SampleHandle handle, Args&&... args)
    {
        if (handle == SampleHandle::Invalid) {
            return false;
        }
        return storage->at(static_cast<size_t>(handle)).push_walltime(std::forward<Args>(args)...);
    }

    template<typename... Args>
    static inline bool push_cputime(SampleHandle handle, Args&&... args)
    {
        if (handle == SampleHandle::Invalid) {
            return false;
        }
        return storage->at(static_cast<size_t>(handle)).push_cputime(std::forward<Args>(args)...);
    }

    template<typename... Args>
    static inline bool push_acquire(SampleHandle handle, Args&&... args)
    {
        if (handle == SampleHandle::Invalid) {
            return false;
        }
        return storage->at(static_cast<size_t>(handle)).push_acquire(std::forward<Args>(args)...);
    }

    template<typename... Args>
    static inline bool push_release(SampleHandle handle, Args&&... args)
    {
        if (handle == SampleHandle::Invalid) {
            return false;
        }
        return storage->at(static_cast<size_t>(handle)).push_release(std::forward<Args>(args)...);
    }

    template<typename... Args>
    static inline bool push_alloc(SampleHandle handle, Args&&... args)
    {
        if (handle == SampleHandle::Invalid) {
            return false;
        }
        return storage->at(static_cast<size_t>(handle)).push_alloc(std::forward<Args>(args)...);
    }

    template<typename... Args>
    static inline bool push_heap(SampleHandle handle, Args&&... args)
    {
        if (handle == SampleHandle::Invalid) {
            return false;
        }
        return storage->at(static_cast<size_t>(handle)).push_heap(std::forward<Args>(args)...);
    }

    template<typename... Args>
    static inline bool push_lock_name(SampleHandle handle, Args&&... args)
    {
        if (handle == SampleHandle::Invalid) {
            return false;
        }
        return storage->at(static_cast<size_t>(handle)).push_lock_name(std::forward<Args>(args)...);
    }

    template<typename... Args>
    static inline bool push_threadinfo(SampleHandle handle, Args&&... args)
    {
        if (handle == SampleHandle::Invalid) {
            return false;
        }
        return storage->at(static_cast<size_t>(handle)).push_threadinfo(std::forward<Args>(args)...);
    }

    template<typename... Args>
    static inline bool push_task_id(SampleHandle handle, Args&&... args)
    {
        if (handle == SampleHandle::Invalid) {
            return false;
        }
        return storage->at(static_cast<size_t>(handle)).push_task_id(std::forward<Args>(args)...);
    }

    template<typename... Args>
    static inline bool push_task_name(SampleHandle handle, Args&&... args)
    {
        if (handle == SampleHandle::Invalid) {
            return false;
        }
        return storage->at(static_cast<size_t>(handle)).push_task_name(std::forward<Args>(args)...);
    }

    template<typename... Args>
    static inline bool push_span_id(SampleHandle handle, Args&&... args)
    {
        if (handle == SampleHandle::Invalid) {
            return false;
        }
        return storage->at(static_cast<size_t>(handle)).push_span_id(std::forward<Args>(args)...);
    }

    template<typename... Args>
    static inline bool push_local_root_span_id(SampleHandle handle, Args&&... args)
    {
        if (handle == SampleHandle::Invalid) {
            return false;
        }
        return storage->at(static_cast<size_t>(handle)).push_local_root_span_id(std::forward<Args>(args)...);
    }

    template<typename... Args>
    static inline bool push_trace_type(SampleHandle handle, Args&&... args)
    {
        if (handle == SampleHandle::Invalid) {
            return false;
        }
        return storage->at(static_cast<size_t>(handle)).push_trace_type(std::forward<Args>(args)...);
    }

    template<typename... Args>
    static inline bool push_trace_resource_container(SampleHandle handle, Args&&... args)
    {
        if (handle == SampleHandle::Invalid) {
            return false;
        }
        return storage->at(static_cast<size_t>(handle)).push_trace_resource_container(std::forward<Args>(args)...);
    }

    template<typename... Args>
    static inline bool push_exceptioninfo(SampleHandle handle, Args&&... args)
    {
        if (handle == SampleHandle::Invalid) {
            return false;
        }
        return storage->at(static_cast<size_t>(handle)).push_exceptioninfo(std::forward<Args>(args)...);
    }

    template<typename... Args>
    static inline bool push_class_name(SampleHandle handle, Args&&... args)
    {
        if (handle == SampleHandle::Invalid) {
            return false;
        }
        return storage->at(static_cast<size_t>(handle)).push_class_name(std::forward<Args>(args)...);
    }

    template<typename... Args>
    static inline void push_frame(SampleHandle handle, Args&&... args)
    {
        if (handle == SampleHandle::Invalid) {
            return;
        }
        storage->at(static_cast<size_t>(handle)).push_frame(std::forward<Args>(args)...);
    }

    template<typename... Args>
    static inline bool flush_sample(SampleHandle handle, Args&&... args)
    {
        if (handle == SampleHandle::Invalid) {
            return false;
        }
        auto ret = storage->at(static_cast<size_t>(handle)).flush_sample(std::forward<Args>(args)...);

        // When we flush, we're done with the sample handle and can release it
        release_handler(handle);
        return ret;
    }
};

} // namespace Datadog
