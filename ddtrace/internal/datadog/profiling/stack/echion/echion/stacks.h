// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <unordered_set>
#include <utility>
#include <vector>

#include <echion/config.h>
#include <echion/frame.h>
#include <echion/task_name.h>
#if PY_VERSION_HEX >= 0x030b0000
#include "echion/stack_chunk.h"
#endif // PY_VERSION_HEX >= 0x030b0000
#include <echion/errors.h>

class EchionSampler;

// ----------------------------------------------------------------------------
// FrameStack owns the Frames so that they stay valid across cache evictions
// (asyncio unwind_tasks precomputes per-task stacks via Frame::get, which can
// evict entries still referenced from an earlier thread-stack capture).
class FrameStack : public std::vector<Frame>
{
  public:
    using Key = Frame::Key;

    void render(EchionSampler& echion);
};

// Forward declaration
class EchionSampler;

// ----------------------------------------------------------------------------
// Primary entry point. The caller supplies the cycle-detection set; callers on
// the sampling thread should pass EchionSampler::seen_frames_scratch() so the
// hash table's capacity is reused across calls instead of reallocated per call.
// `seen_frames` is cleared on entry.
size_t
unwind_frame(EchionSampler& echion,
             PyObject* frame_addr,
             FrameStack& stack,
             std::unordered_set<PyObject*>& seen_frames,
             size_t max_depth = max_frames);

// Convenience variant that owns a local scratch set, for callers that have no
// reusable scratch to share (fuzz harnesses and other callers outside the
// sampling thread). Prefer the primary overload above on the sampling thread.
size_t
unwind_frame(EchionSampler& echion, PyObject* frame_addr, FrameStack& stack, size_t max_depth = max_frames);

// ----------------------------------------------------------------------------
void
unwind_python_stack(EchionSampler& echion, PyThreadState* tstate, FrameStack& stack);

// ----------------------------------------------------------------------------
class StackInfo
{
  public:
    TaskName task_name;
    bool on_cpu;
    FrameStack stack;

    StackInfo(TaskName task_name, bool on_cpu)
      : task_name(std::move(task_name))
      , on_cpu(on_cpu)
    {
    }
};
