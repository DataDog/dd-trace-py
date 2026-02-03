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

class ThreadInfo
{
  public:
    using Ptr = std::unique_ptr<ThreadInfo>;

    uintptr_t thread_id;
    unsigned long native_id;
    FrameStack python_stack;
    std::vector<std::unique_ptr<StackInfo>> current_tasks;
    std::vector<std::unique_ptr<StackInfo>> current_greenlets;

    std::string name;

#if defined PL_LINUX
    clockid_t cpu_clock_id;
#elif defined PL_DARWIN
    mach_port_t mach_port;
#endif
    microsecond_t cpu_time;

    uintptr_t asyncio_loop = 0;
    uintptr_t tstate_addr = 0; // Remote address of PyThreadState for accessing asyncio_tasks_head

    [[nodiscard]] Result<void> update_cpu_time();

    [[nodiscard]] Result<void> sample(EchionSampler&, int64_t, PyThreadState*, microsecond_t);
    void unwind(EchionSampler&, PyThreadState*);

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
    [[nodiscard]] Result<void> unwind_tasks(EchionSampler&, PyThreadState*);
    void unwind_greenlets(EchionSampler&, PyThreadState*, unsigned long);
    [[nodiscard]] Result<std::vector<TaskInfo::Ptr>> get_all_tasks(EchionSampler&, PyThreadState* tstate);
#if PY_VERSION_HEX >= 0x030e0000
    [[nodiscard]] Result<void> get_tasks_from_thread_linked_list(EchionSampler& echion,
                                                                 std::vector<TaskInfo::Ptr>& tasks);
    [[nodiscard]] Result<void> get_tasks_from_interpreter_linked_list(EchionSampler& echion,
                                                                      PyThreadState* tstate,
                                                                      std::vector<TaskInfo::Ptr>& tasks);
    [[nodiscard]] Result<void> get_tasks_from_linked_list(EchionSampler& echion,
                                                          uintptr_t head_addr,
                                                          std::vector<TaskInfo::Ptr>& tasks);
#endif
};

// ----------------------------------------------------------------------------

using PyThreadStateCallback = std::function<void(PyThreadState*, ThreadInfo&)>;

void
for_each_thread(EchionSampler& echion, InterpreterInfo& interp, PyThreadStateCallback callback);
