// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2025 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#define Py_BUILD_CORE
#include <Python.h>

#if PY_VERSION_HEX >= 0x030e0000
#include <internal/pycore_frame.h>
#endif

#include <echion/stacks.h>
#include <echion/strings.h>

#include <utility>
#include <vector>

#define FRAME_NOT_SET Py_False // Sentinel for frame cell

class EchionSampler;

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

    void unwind(EchionSampler& echion, PyObject*, PyThreadState*, FrameStack&);
};

// Lightweight snapshot of a greenlet's state for unwinding outside the lock.
// Frame pointers may become stale after the lock is released (e.g. if the
// greenlet finishes and the PyFrameObject is freed).  GreenletInfo::unwind()
// reads through them using copy_type(), which safely handles invalid addresses.
struct GreenletSnapshot
{
    GreenletInfo::ID greenlet_id;
    StringTable::Key name;
    PyObject* frame; // potentially-stale address, read via copy_type in unwind
    // Parent chain: (parent_name, parent_frame) pairs in order from immediate parent up
    std::vector<std::pair<StringTable::Key, PyObject*>> parent_chain;
};
