// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <deque>

#include <echion/config.h>
#include <echion/frame.h>
#if PY_VERSION_HEX >= 0x030b0000
#include "echion/stack_chunk.h"
#endif // PY_VERSION_HEX >= 0x030b0000
#include <echion/errors.h>

// ----------------------------------------------------------------------------
class FrameStack : public std::deque<Frame::Ref>
{
  public:
    using Key = Frame::Key;

    // ------------------------------------------------------------------------
    void render()
    {
        for (auto it = this->rbegin(); it != this->rend(); ++it) {
#if PY_VERSION_HEX >= 0x030c0000
            if ((*it).get().is_entry)
                // This is a shim frame so we skip it.
                continue;
#endif
            Renderer::get().render_frame((*it).get());
        }
    }
};

// ----------------------------------------------------------------------------
#if PY_VERSION_HEX >= 0x030b0000
size_t
unwind_frame(StackChunk* stack_chunk, PyObject* frame_addr, FrameStack& stack, size_t max_depth = max_frames);
#else
size_t
unwind_frame(PyObject* frame_addr, FrameStack& stack, size_t max_depth = max_frames);
#endif

// ----------------------------------------------------------------------------
class EchionSampler; // forward declaration

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
