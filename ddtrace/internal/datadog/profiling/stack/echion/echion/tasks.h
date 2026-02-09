// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#define Py_BUILD_CORE

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <frameobject.h>

#if PY_VERSION_HEX >= 0x030b0000
#include <cpython/genobject.h>

#include <cstddef>
#if PY_VERSION_HEX >= 0x030e0000
#include <internal/pycore_frame.h>
#include <opcode.h>
#elif PY_VERSION_HEX >= 0x030d0000
#include <opcode.h>
#else
#include <internal/pycore_frame.h>
#include <internal/pycore_opcode.h>
#endif // PY_VERSION_HEX >= 0x030d0000
#else
#include <genobject.h>
#include <opcode.h>
#endif // PY_VERSION_HEX >= 0x30b0000

#include <mutex>
#include <unordered_map>
#include <vector>

#include <echion/config.h>
#include <echion/errors.h>
#include <echion/frame.h>
#include <echion/mirrors.h>
#include <echion/stacks.h>
#include <echion/state.h>
#include <echion/strings.h>
#include <echion/timing.h>

#include <echion/cpython/tasks.h>

// Max number of recursive calls GenInfo::GenInfo and TaskInfo::TaskInfo can do
// before raising an error.
const constexpr size_t MAX_RECURSION_DEPTH = 250;

// This is a private type in CPython, so we need to define it here
// in order to be able to use it in our code.
// We cannot put it into namespace {} because the 'PyAsyncGenASend' name would then be ambiguous.
// The extern "C" is not required but here to avoid any ambiguity.
extern "C"
{

    typedef struct PyAsyncGenASend
    {
        PyObject_HEAD PyAsyncGenObject* ags_gen;
    } PyAsyncGenASend;

#ifndef PyAsyncGenASend_CheckExact
#if PY_VERSION_HEX >= 0x03090000
// Py_IS_TYPE is only available since Python 3.9
#define PyAsyncGenASend_CheckExact(obj) (Py_IS_TYPE(obj, &_PyAsyncGenASend_Type))
#else // PY_VERSION_HEX >= 0x03090000
#define PyAsyncGenASend_CheckExact(obj) (Py_TYPE(obj) == &_PyAsyncGenASend_Type)
#endif // PY_VERSION_HEX < 0x03090000
#endif // defined PyAsyncGenASend_CheckExact
}

class GenInfo
{
  public:
    typedef std::unique_ptr<GenInfo> Ptr;

    // The address of the Task PyObject* the GenInfo represents
    PyObject* origin = nullptr;

    // The address of the Frame PyObject* the GenInfo represents
    PyObject* frame = nullptr;

    // The coroutine awaited by this coroutine, if any
    GenInfo::Ptr await = nullptr;

    // Whether the coroutine, or the coroutine it awaits, is currently running (on CPU)
    bool is_running = false;

    [[nodiscard]] static Result<GenInfo::Ptr> create(PyObject* gen_addr);
    GenInfo(PyObject* origin, PyObject* frame, GenInfo::Ptr await, bool is_running)
      : origin(origin)
      , frame(frame)
      , await(std::move(await))
      , is_running(is_running)
    {
    }
};

// ----------------------------------------------------------------------------

class TaskInfo
{
  public:
    typedef std::unique_ptr<TaskInfo> Ptr;
    typedef std::reference_wrapper<TaskInfo> Ref;

    // The address of the Task PyObject* the TaskInfo represents
    PyObject* origin = nullptr;

    // The address of the asyncio Event Loop PyObject* the Task is running on
    PyObject* loop = nullptr;

    // The name of the Task
    StringTable::Key name;

    // Whether the Task's coroutine (or a coroutine it awaits, transitively) is currently running (on CPU).
    // This will not be true if the Task is currently awaiting another Task, and this other Task is on CPU.
    bool is_on_cpu = false;

    // The coroutine wrapped by the Task, i.e. the coroutine passed to asyncio.create_task
    // or equivalent (e.g. asyncio.run, etc.)
    GenInfo::Ptr coro = nullptr;

    // The Task that the current Task is awaiting, if any.
    // Note that this will not be set if the current Task's coroutine is awaiting another coroutine,
    // only if it is awaiting another Task.
    TaskInfo::Ptr waiter = nullptr;

    [[nodiscard]] static Result<TaskInfo::Ptr> create(EchionSampler& echion, TaskObj*);
    TaskInfo(PyObject* origin, PyObject* loop, GenInfo::Ptr coro, StringTable::Key name, TaskInfo::Ptr waiter)
      : origin(origin)
      , loop(loop)
      , name(name)
      , is_on_cpu(coro && coro->is_running)
      , coro(std::move(coro))
      , waiter(std::move(waiter))
    {
    }

    size_t unwind(EchionSampler& echion, FrameStack&);
};
