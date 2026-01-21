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

    // Asyncio state
    PyObject* asyncio_scheduled_tasks_ = nullptr;
    PyObject* asyncio_eager_tasks_ = nullptr;

    // Flag to indicate that uvloop is in use.
    // When true, the stack unwinding logic uses Runner.run as the boundary frame
    // instead of Handle._run, and skips the uvloop wrapper frame.
    bool using_uvloop_ = false;

    // Task unwinding state
    std::optional<Frame::Key> asyncio_frame_cache_key_;
    std::optional<Frame::Key> uvloop_frame_cache_key_;
    std::unordered_set<PyObject*> previous_task_objects_;

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

    PyObject* asyncio_scheduled_tasks() const { return asyncio_scheduled_tasks_; }
    PyObject* asyncio_eager_tasks() const { return asyncio_eager_tasks_; }

    bool using_uvloop() const { return using_uvloop_; }
    void set_using_uvloop(bool value) { using_uvloop_ = value; }

    void init_asyncio(PyObject* scheduled_tasks, PyObject* eager_tasks)
    {
        asyncio_scheduled_tasks_ = scheduled_tasks;
        asyncio_eager_tasks_ = (eager_tasks != Py_None) ? eager_tasks : nullptr;
    }

    std::optional<Frame::Key>& asyncio_frame_cache_key() { return asyncio_frame_cache_key_; }
    std::optional<Frame::Key>& uvloop_frame_cache_key() { return uvloop_frame_cache_key_; }
    std::unordered_set<PyObject*>& previous_task_objects() { return previous_task_objects_; }

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
    }
};
