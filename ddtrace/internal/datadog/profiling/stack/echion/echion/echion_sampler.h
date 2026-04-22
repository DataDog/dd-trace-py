#pragma once

#include <cstdint>
#include <optional>
#include <unordered_map>
#include <unordered_set>

#include <echion/cache.h>
#include <echion/frame.h>
#include <echion/strings.h>
#include <echion/threads.h>

#include "stack_renderer.hpp"

// Forward declaration
class Frame;

class EchionSampler
{
    // Thread Info map (Thread ID -> ThreadInfo)
    std::unordered_map<uintptr_t, ThreadInfo::Ptr> thread_info_map_;
    std::mutex thread_info_map_lock_;

    // Task Link maps (Task -> Task relationships)
    std::unordered_map<PyObject*, PyObject*> task_link_map_;
    std::unordered_map<PyObject*, PyObject*> weak_task_link_map_;
    std::mutex task_link_map_lock_;

    // Greenlet maps
    std::unordered_map<GreenletInfo::ID, GreenletInfo::Ptr> greenlet_info_map_;
    std::unordered_map<GreenletInfo::ID, GreenletInfo::ID> greenlet_parent_map_;
    std::unordered_map<uintptr_t, GreenletInfo::ID> greenlet_thread_map_;
    std::mutex greenlet_info_map_lock_;

    // Greenlet struct offset discovery: allows reading gr_frame and
    // stack_stop at sample time instead of caching per-switch via
    // update_greenlet_frame().  The greenlet struct uses pimpl indirection:
    //   PyGreenlet + pimpl_offset       -> Greenlet* (pimpl)
    //   Greenlet*  + frame_offset       -> struct _frame* (gr_frame)
    //   Greenlet*  + stack_stop_offset -> char* (stack_stop, non-NULL = active)
    size_t greenlet_pimpl_offset_ = 0;
    size_t greenlet_frame_offset_ = 0;
    size_t greenlet_stack_stop_offset_ = 0;
    bool greenlet_offsets_valid_ = false;

    // Asyncio state
    PyObject* asyncio_scheduled_tasks_ = nullptr;
    PyObject* asyncio_eager_tasks_ = nullptr;

    // Task unwinding state
    std::optional<Frame::Key> asyncio_frame_cache_key_;
    std::optional<Frame::Key> uvloop_frame_cache_key_;
    std::unordered_set<PyObject*> previous_task_objects_;

    // Accumulated asyncio task count across sampled threads in the current sampling cycle.
    // When thread subsampling is enabled (_DD_PROFILING_STACK_MAX_THREADS), this only
    // reflects tasks from the sampled subset, not all threads in the process.
    // Only accessed from the sampling thread, so no lock/atomic is needed.
    size_t asyncio_task_count_ = 0;

    // Caches
    StringTable string_table_;
    LRUCache<uintptr_t, Frame> frame_cache_;

    // Stack renderer for outputting samples
    Datadog::StackRenderer renderer_;

  public:
    EchionSampler(size_t frame_cache_capacity = 1024)
      : frame_cache_(frame_cache_capacity)
    {
    }
    ~EchionSampler() = default;

    Datadog::StackRenderer& renderer() { return renderer_; }

    std::unordered_map<uintptr_t, ThreadInfo::Ptr>& thread_info_map() { return thread_info_map_; }
    std::mutex& thread_info_map_lock() { return thread_info_map_lock_; }

    std::unordered_map<PyObject*, PyObject*>& task_link_map() { return task_link_map_; }
    std::unordered_map<PyObject*, PyObject*>& weak_task_link_map() { return weak_task_link_map_; }
    std::mutex& task_link_map_lock() { return task_link_map_lock_; }

    std::unordered_map<GreenletInfo::ID, GreenletInfo::Ptr>& greenlet_info_map() { return greenlet_info_map_; }
    std::unordered_map<GreenletInfo::ID, GreenletInfo::ID>& greenlet_parent_map() { return greenlet_parent_map_; }
    std::unordered_map<uintptr_t, GreenletInfo::ID>& greenlet_thread_map() { return greenlet_thread_map_; }
    std::mutex& greenlet_info_map_lock() { return greenlet_info_map_lock_; }

    void set_greenlet_offsets(size_t pimpl_offset, size_t frame_offset, size_t stack_stop_offset)
    {
        greenlet_pimpl_offset_ = pimpl_offset;
        greenlet_frame_offset_ = frame_offset;
        greenlet_stack_stop_offset_ = stack_stop_offset;
        greenlet_offsets_valid_ = true;
    }
    size_t greenlet_pimpl_offset() const { return greenlet_pimpl_offset_; }
    size_t greenlet_frame_offset() const { return greenlet_frame_offset_; }
    size_t greenlet_stack_stop_offset() const { return greenlet_stack_stop_offset_; }
    bool greenlet_offsets_valid() const { return greenlet_offsets_valid_; }

    PyObject* asyncio_scheduled_tasks() const { return asyncio_scheduled_tasks_; }
    PyObject* asyncio_eager_tasks() const { return asyncio_eager_tasks_; }

    void init_asyncio(PyObject* scheduled_tasks, PyObject* eager_tasks)
    {
        asyncio_scheduled_tasks_ = scheduled_tasks;
        asyncio_eager_tasks_ = (eager_tasks != Py_None) ? eager_tasks : nullptr;
    }

    std::optional<Frame::Key>& asyncio_frame_cache_key() { return asyncio_frame_cache_key_; }
    std::optional<Frame::Key>& uvloop_frame_cache_key() { return uvloop_frame_cache_key_; }
    std::unordered_set<PyObject*>& previous_task_objects() { return previous_task_objects_; }

    void reset_asyncio_task_count() { asyncio_task_count_ = 0; }
    void add_asyncio_task_count(size_t count) { asyncio_task_count_ += count; }
    size_t asyncio_task_count() const { return asyncio_task_count_; }

    // Accessor for StringTable operations
    StringTable& string_table() { return string_table_; }
    const StringTable& string_table() const { return string_table_; }

    // Accessor for frame cache operations
    LRUCache<uintptr_t, Frame>& frame_cache() { return frame_cache_; }

    void postfork_child()
    {
        // Re-init mutexes (placement new to avoid UB)
        new (&thread_info_map_lock_) std::mutex;
        new (&task_link_map_lock_) std::mutex;
        new (&greenlet_info_map_lock_) std::mutex;

        // Reset string_table mutex
        string_table_.postfork_child();

        // Clear frame cache after fork (prevent stale pointers)
        frame_cache_.clear();

        // Clear stale entries from parent process.
        // No lock needed: only one thread exists in child immediately after fork.
        task_link_map_.clear();
        weak_task_link_map_.clear();
        greenlet_info_map_.clear();
        greenlet_parent_map_.clear();
        greenlet_thread_map_.clear();

        // Clear renderer caches to avoid using stale interned IDs from the
        // parent's Profiles Dictionary
        renderer_.postfork_child();
    }
};
