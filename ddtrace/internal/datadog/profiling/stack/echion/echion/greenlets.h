// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2025 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#include <Python.h>
#define Py_BUILD_CORE

#if PY_VERSION_HEX >= 0x030e0000
#include <internal/pycore_frame.h>
#endif

#include <echion/stacks.h>
#include <echion/strings.h>

#define FRAME_NOT_SET Py_False // Sentinel for frame cell

class GreenletInfo
{
  public:
    typedef std::unique_ptr<GreenletInfo> Ptr;
    typedef std::reference_wrapper<GreenletInfo> Ref;
    typedef uintptr_t ID;

    ID greenlet_id = 0;
    StringTable::Key name;
    PyObject* frame = NULL;

    GreenletInfo(ID id, PyObject* frame, StringTable::Key name)
      : greenlet_id(id)
      , name(name)
      , frame(frame)
    {
    }

    int unwind(PyObject*, PyThreadState*, FrameStack&);
};

// ----------------------------------------------------------------------------

inline int
GreenletInfo::unwind(PyObject* frame, PyThreadState* tstate, FrameStack& stack)
{
    PyObject* frame_addr = NULL;
#if PY_VERSION_HEX >= 0x030d0000
    frame_addr = frame == Py_None ? reinterpret_cast<PyObject*>(tstate->current_frame)
                                  : reinterpret_cast<PyObject*>(reinterpret_cast<struct _frame*>(frame)->f_frame);
#elif PY_VERSION_HEX >= 0x030b0000
    if (frame == Py_None) {
        _PyCFrame cframe;
        _PyCFrame* cframe_addr = tstate->cframe;
        if (copy_type(cframe_addr, cframe))
            // TODO: Invalid frame
            return 0;

        frame_addr = reinterpret_cast<PyObject*>(cframe.current_frame);
    } else {
        frame_addr = reinterpret_cast<PyObject*>(reinterpret_cast<struct _frame*>(frame)->f_frame);
    }

#else // Python < 3.11
    frame_addr = frame == Py_None ? reinterpret_cast<PyObject*>(tstate->frame) : frame;
#endif
    auto count = unwind_frame(frame_addr, stack);

    stack.push_back(Frame::get(name));

    return count + 1; // We add an extra count for the frame with the greenlet
                      // name.
}

// ----------------------------------------------------------------------------

// We make this a reference to a heap-allocated object so that we can avoid
// the destruction on exit. We are in charge of cleaning up the object. Note
// that the object will leak, but this is not a problem.
inline std::unordered_map<GreenletInfo::ID, GreenletInfo::Ptr>& greenlet_info_map =
  *(new std::unordered_map<GreenletInfo::ID, GreenletInfo::Ptr>());

// maps greenlets to their parent
inline std::unordered_map<GreenletInfo::ID, GreenletInfo::ID>& greenlet_parent_map =
  *(new std::unordered_map<GreenletInfo::ID, GreenletInfo::ID>());

// maps threads to any currently active greenlets
inline std::unordered_map<uintptr_t, GreenletInfo::ID>& greenlet_thread_map =
  *(new std::unordered_map<uintptr_t, GreenletInfo::ID>());

inline std::mutex greenlet_info_map_lock;

// ----------------------------------------------------------------------------

inline std::vector<std::unique_ptr<StackInfo>> current_greenlets;

// ----------------------------------------------------------------------------
