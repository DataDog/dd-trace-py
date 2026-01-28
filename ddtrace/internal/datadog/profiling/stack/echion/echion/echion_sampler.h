#pragma once

#include <cstdint>
#include <optional>
#include <unordered_map>
#include <unordered_set>

#include <echion/cache.h>
#include <echion/threads.h>

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

    // Task unwinding state
    std::optional<Frame::Key> frame_cache_key_;
    std::unordered_set<PyObject*> previous_task_objects_;

    // String table for caching Python strings (filenames, function names, etc.)
    StringTable string_table_;

    // Frame cache for caching Frame objects
    std::unique_ptr<LRUCache<uintptr_t, Frame>> frame_cache_;

  public:
    EchionSampler() = default;
    ~EchionSampler() = default;

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

    void init_asyncio(PyObject* scheduled_tasks, PyObject* eager_tasks)
    {
        asyncio_scheduled_tasks_ = scheduled_tasks;
        asyncio_eager_tasks_ = (eager_tasks != Py_None) ? eager_tasks : nullptr;
    }

    std::optional<Frame::Key>& frame_cache_key() { return frame_cache_key_; }
    std::unordered_set<PyObject*>& previous_task_objects() { return previous_task_objects_; }

    StringTable& string_table() { return string_table_; }
    const StringTable& string_table() const { return string_table_; }

    LRUCache<uintptr_t, Frame>& frame_cache() { return *frame_cache_; }
    const LRUCache<uintptr_t, Frame>& frame_cache() const { return *frame_cache_; }

    void init_frame_cache(size_t capacity) { frame_cache_ = std::make_unique<LRUCache<uintptr_t, Frame>>(capacity); }

    void postfork_child()
    {
        // Re-init mutexes (placement new to avoid UB)
        new (&thread_info_map_lock_) std::mutex;
        new (&task_link_map_lock_) std::mutex;
        new (&greenlet_info_map_lock_) std::mutex;

        // Clear stale entries from parent process.
        // No lock needed: only one thread exists in child immediately after fork.
        task_link_map_.clear();
        weak_task_link_map_.clear();
        greenlet_info_map_.clear();
        greenlet_parent_map_.clear();
        greenlet_thread_map_.clear();

        // Reset the string_table mutex to avoid deadlock if fork happened while it was held
        string_table_.postfork_child();
    }
};
