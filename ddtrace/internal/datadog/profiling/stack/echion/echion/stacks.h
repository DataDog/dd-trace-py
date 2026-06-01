// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <unordered_set>
#include <vector>

#include <echion/config.h>
#include <echion/frame.h>
#if PY_VERSION_HEX >= 0x030b0000
#include "echion/stack_chunk.h"
#endif // PY_VERSION_HEX >= 0x030b0000
#include <echion/errors.h>

class EchionSampler;

// ----------------------------------------------------------------------------
// FrameStack owns the Frames so that they stay valid across cache evictions
// (asyncio unwind_tasks precomputes per-task stacks via Frame::get, which can
// evict entries still referenced from an earlier thread-stack capture).
//
// Backed by std::vector<Frame> rather than std::deque<Frame>: every call site
// uses only push_back, forward iteration, size(), clear(), and integer
// indexing, and none retains references across mutations. Vector keeps stacks
// in a single contiguous buffer and lets the buffer be reused via clear()
// (which preserves capacity), avoiding deque's per-instance chunk-map layout.
class FrameStack : public std::vector<Frame>
{
  public:
    using Key = Frame::Key;

    void render(EchionSampler& echion);
};

// Forward declaration
class EchionSampler;

// ----------------------------------------------------------------------------
// Scratch-buffer-reusing variant. Callers on the sampling thread should pass
// EchionSampler::seen_frames_scratch() to avoid per-call hash-table allocation.
// `seen_frames` is cleared on entry.
size_t
unwind_frame(EchionSampler& echion,
             PyObject* frame_addr,
             FrameStack& stack,
             std::unordered_set<PyObject*>& seen_frames,
             size_t max_depth = max_frames);

// Convenience wrapper that owns a local scratch set (used by fuzz harnesses
// and other callers outside the sampling thread).
size_t
unwind_frame(EchionSampler& echion, PyObject* frame_addr, FrameStack& stack, size_t max_depth = max_frames);

// ----------------------------------------------------------------------------
void
unwind_python_stack(EchionSampler& echion, PyThreadState* tstate, FrameStack& stack);

// ----------------------------------------------------------------------------
class StackInfo
{
  public:
    StringTable::Key task_name;
    bool on_cpu;
    FrameStack stack;

    StackInfo(StringTable::Key task_name, bool on_cpu)
      : task_name(task_name)
      , on_cpu(on_cpu)
    {
    }
};
