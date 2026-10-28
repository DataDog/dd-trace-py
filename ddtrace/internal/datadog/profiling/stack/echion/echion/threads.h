// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

// Py_BUILD_CORE must be defined before Python.h to avoid conflicting
// declarations between public and internal headers (e.g., PyObject_GC_IsFinalized)
#define Py_BUILD_CORE
#include <Python.h>

#if PY_VERSION_HEX >= 0x030e0000
#include <internal/pycore_tstate.h>
#endif

#include <cstdint>
#include <functional>
#include <mutex>
#include <unordered_map>

#if defined PL_LINUX
#include <ctime>
#elif defined PL_DARWIN
#include <mach/clock.h>
#include <mach/mach.h>
#endif

#include <echion/errors.h>
#include <echion/greenlets.h>
#include <echion/interp.h>
#include <echion/stacks.h>
#include <echion/tasks.h>
#include <echion/timing.h>

class EchionSampler;
class ThreadInfoTaskTraversalTest;

class ThreadInfo
{
  public:
    using Ptr = std::unique_ptr<ThreadInfo>;

    uintptr_t thread_id;
    unsigned long native_id;
    FrameStack python_stack;
    UnwindResult python_stack_unwind_result = UnwindResult::Unknown();
    std::vector<std::unique_ptr<StackInfo>> current_tasks;
    std::vector<std::unique_ptr<StackInfo>> current_greenlets;

    std::string name;

#if defined PL_LINUX
    clockid_t cpu_clock_id;
#elif defined PL_DARWIN
    mach_port_t mach_port;
#endif
    microsecond_t cpu_time = 0;

    // The configured loop: the one set with set_event_loop, created by uvloop.new_event_loop, or attached when the
    // profiler started inside a loop that cannot use the running-loop slot (any loop before Python 3.13, uvloop, or a
    // loop class that overrides run_forever).
    uintptr_t asyncio_loop = 0;
    uintptr_t tstate_addr = 0; // Remote address of PyThreadState for accessing asyncio_tasks_head
    bool using_uvloop = false; // Whether this thread is using uvloop instead of asyncio

    // The running loop: the plain asyncio loop (a BaseEventLoop) whose run_forever is running on this thread right
    // now, or 0. Python hooks on run_forever set and clear it, and on Python 3.13+ so does a profiler start inside a
    // running loop. It exists because a loop can run without ever being configured, for example with
    // asyncio.Runner(loop_factory=...) or a bare loop.run_until_complete(), and because set_event_loop(B) while loop A
    // runs must not make the sampler read B. uvloop loops never go here, so a running loop always uses plain asyncio
    // unwinding.
    uintptr_t running_asyncio_loop = 0;

    // Sets the running loop. An owned call comes from a run_forever hook that has already checked that no loop runs
    // on this thread, so any existing value is stale and is replaced. An unowned call comes from the profiler
    // starting inside a loop that is already running: it fills an empty slot only, so it never replaces the loop
    // that a hook registered. Returns whether the slot was set.
    bool enter_running_loop(uintptr_t loop, bool unowned) noexcept
    {
        if (unowned && running_asyncio_loop != 0) {
            return false;
        }
        running_asyncio_loop = loop;
        return true;
    }

    // Clears the running loop if it still holds loop. Example: if loop B replaced A, exiting A leaves B in place.
    void exit_running_loop(uintptr_t loop) noexcept
    {
        if (running_asyncio_loop == loop) {
            running_asyncio_loop = 0;
        }
    }

    // The loop and uvloop mode that the sampler uses for this thread: the running loop if one is set, otherwise the
    // configured loop. The sampler holds thread_info_map_lock for a whole unwind, and the setters take the same lock,
    // so every read during one unwind sees the same values.
    [[nodiscard]] uintptr_t effective_loop() const noexcept
    {
        return running_asyncio_loop != 0 ? running_asyncio_loop : asyncio_loop;
    }
    [[nodiscard]] bool effective_uvloop() const noexcept { return running_asyncio_loop != 0 ? false : using_uvloop; }

    [[nodiscard]] Result<void> update_cpu_time();

    [[nodiscard]] Result<void> sample(EchionSampler&, PyThreadState*, microsecond_t);
    [[nodiscard]] Result<void> unwind(EchionSampler&, PyThreadState*, microsecond_t wall_time_us);

    // Number of frames in python_stack from the asyncio boundary frame (inclusive) up to the root,
    // that is to say the asyncio machinery plus the synchronous entry point. Returns the size of the
    // whole stack when the boundary frame is not there.
    [[nodiscard]] size_t find_upper_python_stack_size(EchionSampler&) const;

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
        // pthread_getcpuclockid() dereferences pthread_t, but Python's thread_id can be
        // a gevent greenlet ID or a stale value. Derive the Linux per-thread clock from
        // the kernel TID instead. clock_gettime() safely rejects an invalid TID with EINVAL.
        constexpr uint64_t CPUCLOCK_SCHED = 2;
        constexpr uint64_t CPUCLOCK_PERTHREAD_MASK = 4;
        auto cpu_clock_id =
          static_cast<clockid_t>((~static_cast<uint64_t>(native_id) << 3) | (CPUCLOCK_SCHED | CPUCLOCK_PERTHREAD_MASK));

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
    using TaskAddressCallback = std::function<Result<void>(TaskObj*)>;

    friend class ThreadInfoTaskTraversalTest;

    void reset_cycle_state() noexcept;
    void render_unwound_stacks(EchionSampler&);
    [[nodiscard]] Result<void> unwind_tasks(EchionSampler&,
                                            PyThreadState*,
                                            microsecond_t wall_time_us,
                                            const Datadog::LogicalSpanReader& spans);
    void unwind_greenlets(EchionSampler&,
                          PyThreadState*,
                          unsigned long,
                          microsecond_t wall_time_us,
                          const Datadog::LogicalSpanReader& spans);
    [[nodiscard]] Result<std::vector<TaskInfo::Ptr>> get_all_tasks(EchionSampler&, PyThreadState* tstate);
    [[nodiscard]] Result<void> for_each_task_address(EchionSampler&,
                                                     PyThreadState* tstate,
                                                     const TaskAddressCallback& callback);
#if PY_VERSION_HEX >= 0x030e0000
    [[nodiscard]] Result<void> for_each_task_address_from_thread_list(EchionSampler&,
                                                                      const TaskAddressCallback& callback);
    [[nodiscard]] Result<void> for_each_task_address_from_interpreter_list(EchionSampler&,
                                                                           PyThreadState* tstate,
                                                                           const TaskAddressCallback& callback);
    [[nodiscard]] Result<std::vector<TaskObj*>> get_task_addresses_from_linked_list(uintptr_t head_addr);
#endif
};

// ----------------------------------------------------------------------------

using PyThreadStateCallback = std::function<void(PyThreadState*, ThreadInfo&)>;

void
for_each_thread(EchionSampler& echion, InterpreterInfo& interp, const PyThreadStateCallback& callback);
