#pragma once

#include <cstdint>

#include <unordered_map>

#include <echion/threads.h>

class EchionSampler
{
    // Thread Info map (Thread ID -> ThreadInfo)
    std::unordered_map<uintptr_t, ThreadInfo::Ptr> thread_info_map_;
    std::mutex thread_info_map_lock_;

    // Task Link map (Task -> Task relationships)
    // std::unordered_map<PyObject*, PyObject*> task_link_map_;
    // std::mutex task_link_map_lock_;

  public:
    EchionSampler() = default;
    ~EchionSampler() = default;

    std::unordered_map<uintptr_t, ThreadInfo::Ptr>& thread_info_map() { return thread_info_map_; }
    std::mutex& thread_info_map_lock() { return thread_info_map_lock_; }

    // std::unordered_map<PyObject*, PyObject*>& task_link_map() { return task_link_map_; }
    // std::mutex& task_link_map_lock() { return task_link_map_lock_; }

    void postfork_child()
    {
        new (&thread_info_map_lock_) std::mutex;
        // new (&task_link_map_lock_) std::mutex;
    }
};
