// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <mutex>
#include <csignal>

#include <echion/stacks.h>
#include <echion/state.h>

// ----------------------------------------------------------------------------

inline std::mutex sigprof_handler_lock;

// ----------------------------------------------------------------------------
inline void sigprof_handler([[maybe_unused]] int signum)
{
#ifndef UNWIND_NATIVE_DISABLE
    unwind_native_stack();
#endif  // UNWIND_NATIVE_DISABLE
    unwind_python_stack(current_tstate);
    // NOTE: Native stacks for tasks is non-trivial, so we skip it for now.

    sigprof_handler_lock.unlock();
}

// ----------------------------------------------------------------------------
inline void sigquit_handler([[maybe_unused]] int signum)
{
    // Wake up the where thread
    std::lock_guard<std::mutex> lock(where_lock);
    where_cv.notify_one();
}

// ----------------------------------------------------------------------------
inline void install_signals()
{
    signal(SIGQUIT, sigquit_handler);

    if (native)
        signal(SIGPROF, sigprof_handler);
}

// ----------------------------------------------------------------------------
inline void restore_signals()
{
    signal(SIGQUIT, SIG_DFL);

    if (native)
        signal(SIGPROF, SIG_DFL);
}
