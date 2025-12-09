// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#include <Python.h>
#define Py_BUILD_CORE

#include <algorithm>
#include <cstdint>
#include <functional>
#include <mutex>
#include <unordered_map>

#if defined PL_LINUX
#include <time.h>
#elif defined PL_DARWIN
#include <mach/clock.h>
#include <mach/mach.h>
#endif

#include <echion/errors.h>
#include <echion/greenlets.h>
#include <echion/interp.h>
#include <echion/render.h>
#include <echion/stacks.h>
#include <echion/tasks.h>
#include <echion/timing.h>

class ThreadInfo
{
  public:
    using Ptr = std::unique_ptr<ThreadInfo>;

    uintptr_t thread_id;
    unsigned long native_id;

    std::string name;

#if defined PL_LINUX
    clockid_t cpu_clock_id;
#elif defined PL_DARWIN
    mach_port_t mach_port;
#endif
    microsecond_t cpu_time;

    uintptr_t asyncio_loop = 0;

    [[nodiscard]] Result<void> update_cpu_time();
    bool is_running();

    [[nodiscard]] Result<void> sample(int64_t, PyThreadState*, microsecond_t);
    void unwind(PyThreadState*);

    // ------------------------------------------------------------------------
#if defined PL_LINUX
    ThreadInfo(uintptr_t thread_id, unsigned long native_id, const char* name, clockid_t cpu_clock_id)
      : thread_id(thread_id)
      , native_id(native_id)
      , name(name)
      , cpu_clock_id(cpu_clock_id)
    {
    }
#elif defined PL_DARWIN
    ThreadInfo(uintptr_t thread_id, unsigned long native_id, const char* name, mach_port_t mach_port)
      : thread_id(thread_id)
      , native_id(native_id)
      , name(name)
      , mach_port(mach_port)
    {
    }
#endif

    [[nodiscard]] static Result<std::unique_ptr<ThreadInfo>> create(uintptr_t thread_id,
                                                                    unsigned long native_id,
                                                                    const char* name)
    {
#if defined PL_LINUX
        clockid_t cpu_clock_id;
        if (pthread_getcpuclockid(static_cast<pthread_t>(thread_id), &cpu_clock_id)) {
            return ErrorKind::ThreadInfoError;
        }

        auto result = std::make_unique<ThreadInfo>(thread_id, native_id, name, cpu_clock_id);
#elif defined PL_DARWIN
        mach_port_t mach_port;
        // pthread_mach_thread_np does not return a status code; the behaviour is undefined
        // if thread_id is invalid.
        mach_port = pthread_mach_thread_np((pthread_t)thread_id);

        auto result = std::make_unique<ThreadInfo>(thread_id, native_id, name, mach_port);
#endif

        auto update_cpu_time_success = result->update_cpu_time();
        if (!update_cpu_time_success) {
            return ErrorKind::ThreadInfoError;
        }

        return result;
    };

  private:
    [[nodiscard]] Result<void> unwind_tasks();
    void unwind_greenlets(PyThreadState*, unsigned long);
};

inline Result<void>
ThreadInfo::update_cpu_time()
{
#if defined PL_LINUX
    struct timespec ts;
    if (clock_gettime(cpu_clock_id, &ts)) {
        // If the clock is invalid, we skip updating the CPU time.
        // This can happen if we try to compute CPU time for a thread that has exited.
        if (errno == EINVAL) {
            return Result<void>::ok();
        }

        return ErrorKind::CpuTimeError;
    }

    this->cpu_time = TS_TO_MICROSECOND(ts);
#elif defined PL_DARWIN
    thread_basic_info_data_t info;
    mach_msg_type_number_t count = THREAD_BASIC_INFO_COUNT;
    kern_return_t kr = thread_info((thread_act_t)this->mach_port, THREAD_BASIC_INFO, (thread_info_t)&info, &count);

    if (kr != KERN_SUCCESS) {
        // If the thread is invalid, we skip updating the CPU time.
        // This can happen if we try to compute CPU time for a thread that has exited.
        if (kr == KERN_INVALID_ARGUMENT) {
            return Result<void>::ok();
        }

        return ErrorKind::CpuTimeError;
    }

    if (info.flags & TH_FLAGS_IDLE) {
        return Result<void>::ok();
    }

    this->cpu_time = TV_TO_MICROSECOND(info.user_time) + TV_TO_MICROSECOND(info.system_time);
#endif

    return Result<void>::ok();
}

inline bool
ThreadInfo::is_running()
{
#if defined PL_LINUX
    struct timespec ts1, ts2;

    // Get two back-to-back times
    if (clock_gettime(cpu_clock_id, &ts1) != 0)
        return false;
    if (clock_gettime(cpu_clock_id, &ts2) != 0)
        return false;

    // If the CPU time has advanced, the thread is running
    return (ts1.tv_sec != ts2.tv_sec || ts1.tv_nsec != ts2.tv_nsec);

#elif defined PL_DARWIN
    thread_basic_info_data_t info;
    mach_msg_type_number_t count = THREAD_BASIC_INFO_COUNT;
    kern_return_t kr = thread_info((thread_act_t)this->mach_port, THREAD_BASIC_INFO, (thread_info_t)&info, &count);

    if (kr != KERN_SUCCESS)
        return false;

    return info.run_state == TH_STATE_RUNNING;

#endif
}

// ----------------------------------------------------------------------------

// We make this a reference to a heap-allocated object so that we can avoid
// the destruction on exit. We are in charge of cleaning up the object. Note
// that the object will leak, but this is not a problem.
inline std::unordered_map<uintptr_t, ThreadInfo::Ptr>& thread_info_map =
  *(new std::unordered_map<uintptr_t, ThreadInfo::Ptr>()); // indexed by thread_id

inline std::mutex thread_info_map_lock;

// ----------------------------------------------------------------------------
inline void
ThreadInfo::unwind(PyThreadState* tstate)
{
    unwind_python_stack(tstate);
    if (asyncio_loop) {
        // unwind_tasks returns a [[nodiscard]] Result<void>.
        // We cast it to void to ignore failures.
        (void)unwind_tasks();
    }

    // We make the assumption that gevent and asyncio are not mixed
    // together to keep the logic here simple. We can always revisit this
    // should there be a substantial demand for it.
    unwind_greenlets(tstate, native_id);
}

// ----------------------------------------------------------------------------
inline Result<void>
ThreadInfo::unwind_tasks()
{
    std::vector<TaskInfo::Ref> leaf_tasks;
    std::unordered_set<PyObject*> parent_tasks;
    std::unordered_map<PyObject*, TaskInfo::Ref> waitee_map; // Indexed by task origin
    std::unordered_map<PyObject*, TaskInfo::Ref> origin_map; // Indexed by task origin
    static std::unordered_set<PyObject*> previous_task_objects;

    auto maybe_all_tasks = get_all_tasks(reinterpret_cast<PyObject*>(asyncio_loop));
    if (!maybe_all_tasks) {
        return ErrorKind::TaskInfoError;
    }

    auto all_tasks = std::move(*maybe_all_tasks);
    {
        std::lock_guard<std::mutex> lock(task_link_map_lock);

        // Clean up the task_link_map. Remove entries associated to tasks that
        // no longer exist.
        std::unordered_set<PyObject*> all_task_origins;
        std::transform(all_tasks.cbegin(),
                       all_tasks.cend(),
                       std::inserter(all_task_origins, all_task_origins.begin()),
                       [](const TaskInfo::Ptr& task) { return task->origin; });

        std::vector<PyObject*> to_remove;
        for (auto kv : task_link_map) {
            if (all_task_origins.find(kv.first) == all_task_origins.end())
                to_remove.push_back(kv.first);
        }
        for (auto key : to_remove) {
            // Only remove the link if the Child Task previously existed; otherwise it's a Task that
            // has just been created and that wasn't in all_tasks when we took the snapshot.
            if (previous_task_objects.find(key) != previous_task_objects.end()) {
                task_link_map.erase(key);
            }
        }

        // Determine the parent tasks from the gather links.
        std::transform(task_link_map.cbegin(),
                       task_link_map.cend(),
                       std::inserter(parent_tasks, parent_tasks.begin()),
                       [](const std::pair<PyObject*, PyObject*>& kv) { return kv.second; });

        // Copy all Task object pointers into previous_task_objects
        previous_task_objects.clear();
        for (const auto& task : all_tasks) {
            previous_task_objects.insert(task->origin);
        }
    }

    for (auto& task : all_tasks) {
        origin_map.emplace(task->origin, std::ref(*task));

        if (task->waiter != NULL)
            waitee_map.emplace(task->waiter->origin, std::ref(*task));
        else if (parent_tasks.find(task->origin) == parent_tasks.end()) {
            if (cpu && ignore_non_running_threads && !task->coro->is_running) {
                // This task is not running, so we skip it if we are
                // interested in just CPU time.
                continue;
            }
            leaf_tasks.push_back(std::ref(*task));
        }
    }

    for (auto& leaf_task : leaf_tasks) {
        bool on_cpu = leaf_task.get().coro->is_running;
        auto stack_info = std::make_unique<StackInfo>(leaf_task.get().name, on_cpu);
        auto& stack = stack_info->stack;
        for (auto current_task = leaf_task;;) {
            auto& task = current_task.get();

            size_t stack_size = task.unwind(stack);

            if (on_cpu) {
                // Undo the stack unwinding
                // TODO[perf]: not super-efficient :(
                for (size_t i = 0; i < stack_size; i++)
                    stack.pop_back();

                // Instead we get part of the thread stack
                FrameStack temp_stack;
                size_t nframes = (python_stack.size() > stack_size) ? python_stack.size() - stack_size : 0;
                for (size_t i = 0; i < nframes; i++) {
                    auto python_frame = python_stack.front();
                    temp_stack.push_front(python_frame);
                    python_stack.pop_front();
                }
                while (!temp_stack.empty()) {
                    stack.push_front(temp_stack.front());
                    temp_stack.pop_front();
                }
            }

            // Add the task name frame
            stack.push_back(Frame::get(task.name));

            // Get the next task in the chain
            PyObject* task_origin = task.origin;
            if (waitee_map.find(task_origin) != waitee_map.end()) {
                current_task = waitee_map.find(task_origin)->second;
                continue;
            }

            {
                // Check for, e.g., gather links
                std::lock_guard<std::mutex> lock(task_link_map_lock);

                if (task_link_map.find(task_origin) != task_link_map.end() &&
                    origin_map.find(task_link_map[task_origin]) != origin_map.end()) {
                    current_task = origin_map.find(task_link_map[task_origin])->second;
                    continue;
                }
            }

            break;
        }

        // Finish off with the remaining thread stack
        for (auto p = python_stack.begin(); p != python_stack.end(); p++)
            stack.push_back(*p);

        current_tasks.push_back(std::move(stack_info));
    }

    return Result<void>::ok();
}

// ----------------------------------------------------------------------------
inline void
ThreadInfo::unwind_greenlets(PyThreadState* tstate, unsigned long cur_native_id)
{
    const std::lock_guard<std::mutex> guard(greenlet_info_map_lock);

    if (greenlet_thread_map.find(cur_native_id) == greenlet_thread_map.end())
        return;

    std::unordered_set<GreenletInfo::ID> parent_greenlets;

    // Collect all parent greenlets
    std::transform(greenlet_parent_map.cbegin(),
                   greenlet_parent_map.cend(),
                   std::inserter(parent_greenlets, parent_greenlets.begin()),
                   [](const std::pair<GreenletInfo::ID, GreenletInfo::ID>& kv) { return kv.second; });

    // Unwind the leaf greenlets
    for (auto& greenlet_info : greenlet_info_map) {
        auto greenlet_id = greenlet_info.first;
        auto& greenlet = greenlet_info.second;

        if (parent_greenlets.find(greenlet_id) != parent_greenlets.end())
            continue;

        auto frame = greenlet->frame;
        if (frame == FRAME_NOT_SET) {
            // The greenlet has not been started yet or has finished
            continue;
        }

        bool on_cpu = frame == Py_None;
        if (cpu && ignore_non_running_threads && !on_cpu) {
            // Only the currently-running greenlet has a None in its frame
            // cell. If we are interested in CPU time, we skip all greenlets
            // that have an actual frame, as they are not running.
            continue;
        }

        auto stack_info = std::make_unique<StackInfo>(greenlet->name, on_cpu);
        auto& stack = stack_info->stack;

        greenlet->unwind(frame, tstate, stack);

        // Unwind the parent greenlets
        for (;;) {
            auto parent_greenlet_info = greenlet_parent_map.find(greenlet_id);
            if (parent_greenlet_info == greenlet_parent_map.end())
                break;

            auto parent_greenlet_id = parent_greenlet_info->second;

            auto parent_greenlet = greenlet_info_map.find(parent_greenlet_id);
            if (parent_greenlet == greenlet_info_map.end())
                break;

            auto parent_frame = parent_greenlet->second->frame;
            if (parent_frame == FRAME_NOT_SET || parent_frame == Py_None)
                break;

            parent_greenlet->second->unwind(parent_frame, tstate, stack);

            // Move up the greenlet chain
            greenlet_id = parent_greenlet_id;
        }

        current_greenlets.push_back(std::move(stack_info));
    }
}

// ----------------------------------------------------------------------------
inline Result<void>
ThreadInfo::sample(int64_t iid, PyThreadState* tstate, microsecond_t delta)
{
    Renderer::get().render_thread_begin(tstate, name, delta, thread_id, native_id);

    if (cpu) {
        microsecond_t previous_cpu_time = cpu_time;
        auto update_cpu_time_success = update_cpu_time();
        if (!update_cpu_time_success) {
            return ErrorKind::CpuTimeError;
        }

        bool thread_is_running = is_running();
        if (!thread_is_running && ignore_non_running_threads) {
            return Result<void>::ok();
        }

        Renderer::get().render_cpu_time(thread_is_running ? cpu_time - previous_cpu_time : 0);
    }

    unwind(tstate);

    // Asyncio tasks
    if (current_tasks.empty()) {
        // If we don't have any asyncio tasks, we check that we don't have any
        // greenlets either. In this case, we print the ordinary thread stack.
        // With greenlets, we recover the thread stack from the active greenlet,
        // so if we don't skip here we would have a double print.
        if (current_greenlets.empty()) {
            // Print the PID and thread name
            Renderer::get().render_stack_begin(pid, iid, name);
            // Print the stack
            python_stack.render();

            Renderer::get().render_stack_end(MetricType::Time, delta);
        }
    } else {
        for (auto& task_stack_info : current_tasks) {
            auto maybe_task_name = string_table.lookup(task_stack_info->task_name);
            if (!maybe_task_name) {
                return ErrorKind::ThreadInfoError;
            }

            const auto& task_name = maybe_task_name->get();
            Renderer::get().render_task_begin(task_name, task_stack_info->on_cpu);
            Renderer::get().render_stack_begin(pid, iid, name);

            task_stack_info->stack.render();

            Renderer::get().render_stack_end(MetricType::Time, delta);
        }

        current_tasks.clear();
    }

    // Greenlet stacks
    if (!current_greenlets.empty()) {
        for (auto& greenlet_stack : current_greenlets) {
            auto maybe_task_name = string_table.lookup(greenlet_stack->task_name);
            if (!maybe_task_name) {
                return ErrorKind::ThreadInfoError;
            }

            const auto& task_name = maybe_task_name->get();
            Renderer::get().render_task_begin(task_name, greenlet_stack->on_cpu);
            Renderer::get().render_stack_begin(pid, iid, name);

            auto& stack = greenlet_stack->stack;
            stack.render();

            Renderer::get().render_stack_end(MetricType::Time, delta);
        }

        current_greenlets.clear();
    }

    return Result<void>::ok();
}

// ----------------------------------------------------------------------------
static void
for_each_thread(InterpreterInfo& interp, std::function<void(PyThreadState*, ThreadInfo&)> callback)
{
    std::unordered_set<PyThreadState*> threads;
    std::unordered_set<PyThreadState*> seen_threads;

    threads.clear();
    seen_threads.clear();

    // Start from the thread list head
    threads.insert(static_cast<PyThreadState*>(interp.tstate_head));

    while (!threads.empty()) {
        // Pop the next thread
        PyThreadState* tstate_addr = *threads.begin();
        threads.erase(threads.begin());

        // Mark the thread as seen
        seen_threads.insert(tstate_addr);

        // Since threads can be created and destroyed at any time, we make
        // a copy of the structure before trying to read its fields.
        PyThreadState tstate;
        if (copy_type(tstate_addr, tstate))
            // We failed to copy the thread so we skip it.
            continue;

        // Enqueue the unseen threads that we can reach from this thread.
        if (tstate.next != NULL && seen_threads.find(tstate.next) == seen_threads.end())
            threads.insert(tstate.next);
        if (tstate.prev != NULL && seen_threads.find(tstate.prev) == seen_threads.end())
            threads.insert(tstate.prev);

        {
            const std::lock_guard<std::mutex> guard(thread_info_map_lock);

            if (thread_info_map.find(tstate.thread_id) == thread_info_map.end()) {
                // If the threading module was not imported in the target then
                // we mistakenly take the hypno thread as the main thread. We
                // assume that any missing thread is the actual main thread,
                // provided we don't already have a thread with the name
                // "MainThread". Note that this can also happen on shutdown, so
                // we need to avoid doing anything in that case.
#if PY_VERSION_HEX >= 0x030b0000
                auto native_id = tstate.native_thread_id;
#else
                auto native_id = getpid();
#endif
                bool main_thread_tracked = false;
                for (auto& kv : thread_info_map) {
                    if (kv.second->name == "MainThread") {
                        main_thread_tracked = true;
                        break;
                    }
                }
                if (main_thread_tracked)
                    continue;

                auto maybe_thread_info = ThreadInfo::create(tstate.thread_id, native_id, "MainThread");
                if (!maybe_thread_info) {
                    // We failed to create the thread info object so we skip it.
                    // We'll likely try again later with the valid thread
                    // information.
                    continue;
                }

                thread_info_map.emplace(tstate.thread_id, std::move(*maybe_thread_info));
            }

            // Call back with the thread state and thread info.
            callback(&tstate, *thread_info_map.find(tstate.thread_id)->second);
        }
    }
}
