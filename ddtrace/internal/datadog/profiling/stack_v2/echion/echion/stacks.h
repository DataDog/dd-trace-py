// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <deque>
#include <unordered_set>

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

inline FrameStack python_stack;

// ----------------------------------------------------------------------------
static size_t
unwind_frame(PyObject* frame_addr, FrameStack& stack)
{
    std::unordered_set<PyObject*> seen_frames; // Used to detect cycles in the stack
    int count = 0;

    PyObject* current_frame_addr = frame_addr;
    while (current_frame_addr != NULL && stack.size() < max_frames) {
        if (seen_frames.find(current_frame_addr) != seen_frames.end())
            break;

        seen_frames.insert(current_frame_addr);

#if PY_VERSION_HEX >= 0x030b0000
        auto maybe_frame = Frame::read(reinterpret_cast<_PyInterpreterFrame*>(current_frame_addr),
                                       reinterpret_cast<_PyInterpreterFrame**>(&current_frame_addr));
#else
        auto maybe_frame = Frame::read(current_frame_addr, &current_frame_addr);
#endif
        if (!maybe_frame) {
            break;
        }

        if (maybe_frame->get().name == StringTable::C_FRAME) {
            continue;
        }

        stack.push_back(*maybe_frame);
        count++;
    }

    return count;
}

// ----------------------------------------------------------------------------
static void
unwind_python_stack(PyThreadState* tstate, FrameStack& stack)
{
    stack.clear();
#if PY_VERSION_HEX >= 0x030b0000
    if (stack_chunk == nullptr) {
        stack_chunk = std::make_unique<StackChunk>();
    }

    if (!stack_chunk->update(reinterpret_cast<_PyStackChunk*>(tstate->datastack_chunk))) {
        stack_chunk = nullptr;
    }
#endif

#if PY_VERSION_HEX >= 0x030d0000
    PyObject* frame_addr = reinterpret_cast<PyObject*>(tstate->current_frame);
#elif PY_VERSION_HEX >= 0x030b0000
    _PyCFrame cframe;
    _PyCFrame* cframe_addr = tstate->cframe;
    if (copy_type(cframe_addr, cframe))
        // TODO: Invalid frame
        return;

    PyObject* frame_addr = reinterpret_cast<PyObject*>(cframe.current_frame);
#else // Python < 3.11
    PyObject* frame_addr = reinterpret_cast<PyObject*>(tstate->frame);
#endif
    unwind_frame(frame_addr, stack);
}

// ----------------------------------------------------------------------------
static void
unwind_python_stack(PyThreadState* tstate)
{
    unwind_python_stack(tstate, python_stack);
}

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
