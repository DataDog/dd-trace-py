// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>

// Sampling interval
inline unsigned int interval = 1000;

// CPU Time mode
inline int cpu = 0;

// For cpu time mode, Echion only unwinds threads that're running by default.
// Set this to false to unwind all threads.
inline bool ignore_non_running_threads = true;

// Maximum number of frames to unwind
inline unsigned int max_frames = 2048;

// ----------------------------------------------------------------------------
static PyObject*
set_interval(PyObject* Py_UNUSED(m), PyObject* args)
{
    unsigned int new_interval;
    if (!PyArg_ParseTuple(args, "I", &new_interval))
        return NULL;

    interval = new_interval;

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
inline void
_set_cpu(int new_cpu)
{
    cpu = new_cpu;
}

// ----------------------------------------------------------------------------
inline void
_set_ignore_non_running_threads(bool new_ignore_non_running_threads)
{
    ignore_non_running_threads = new_ignore_non_running_threads;
}

// ----------------------------------------------------------------------------
static PyObject*
set_cpu(PyObject* Py_UNUSED(m), PyObject* args)
{
    int new_cpu;
    if (!PyArg_ParseTuple(args, "p", &new_cpu))
        return NULL;

    _set_cpu(new_cpu);

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject*
set_max_frames(PyObject* Py_UNUSED(m), PyObject* args)
{
    unsigned int new_max_frames;
    if (!PyArg_ParseTuple(args, "I", &new_max_frames))
        return NULL;

    max_frames = new_max_frames;

    Py_RETURN_NONE;
}
