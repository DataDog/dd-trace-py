// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <deque>
#include <utility>

#include <echion/config.h>
#include <echion/frame.h>
#include <echion/task_label.h>
#if PY_VERSION_HEX >= 0x030b0000
#include "echion/stack_chunk.h"
#endif // PY_VERSION_HEX >= 0x030b0000
#include <echion/errors.h>

class EchionSampler;

// ----------------------------------------------------------------------------
// FrameStack owns the Frames so that they stay valid across cache evictions
// (asyncio unwind_tasks precomputes per-task stacks via Frame::get, which can
// evict entries still referenced from an earlier thread-stack capture).
class FrameStack : public std::deque<Frame>
{
  public:
    using Key = Frame::Key;

    void render(EchionSampler& echion);
};

// Forward declaration
class EchionSampler;

// ----------------------------------------------------------------------------
size_t
unwind_frame(EchionSampler& echion, PyObject* frame_addr, FrameStack& stack, size_t max_depth = max_frames);

// ----------------------------------------------------------------------------
void
unwind_python_stack(EchionSampler& echion, PyThreadState* tstate, FrameStack& stack);

// ----------------------------------------------------------------------------
class StackInfo
{
  public:
    TaskLabel task_name;
    bool on_cpu;
    FrameStack stack;

    StackInfo(TaskLabel task_name, bool on_cpu)
      : task_name(std::move(task_name))
      , on_cpu(on_cpu)
    {
    }
};
