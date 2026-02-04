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

    int unwind(EchionSampler& echion, PyObject*, PyThreadState*, FrameStack&);
};
