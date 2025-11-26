// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <weakrefobject.h>

#if PY_VERSION_HEX >= 0x030b0000
#include <cpython/genobject.h>

#define Py_BUILD_CORE
#if PY_VERSION_HEX >= 0x030d0000
#include <opcode.h>
#else
#include <internal/pycore_opcode.h>
#endif // PY_VERSION_HEX >= 0x030d0000
#else
#include <genobject.h>
#include <opcode.h>
#endif // PY_VERSION_HEX >= 0x30b0000

#include <mutex>
#include <stack>
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

    PyObject* origin = NULL;
    PyObject* frame = NULL;

    GenInfo::Ptr await = nullptr;

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

inline Result<GenInfo::Ptr>
GenInfo::create(PyObject* gen_addr)
{
    static thread_local size_t recursion_depth = 0;
    recursion_depth++;

    if (recursion_depth > MAX_RECURSION_DEPTH) {
        recursion_depth--;
        return ErrorKind::GenInfoError;
    }

    PyGenObject gen;
    if (copy_type(gen_addr, gen)) {
        recursion_depth--;
        return ErrorKind::GenInfoError;
    }

    if (PyAsyncGenASend_CheckExact(&gen)) {
        static_assert(
          sizeof(PyAsyncGenASend) <= sizeof(PyGenObject),
          "PyAsyncGenASend must be smaller than PyGenObject in order for copy_type to have copied enough data.");

        // Type-pun the PyGenObject to a PyAsyncGenASend. *gen_addr was actually never a PyGenObject to begin with,
        // but we do not care as the only thing we will use from it is the ags_gen field.
        PyAsyncGenASend* asend = reinterpret_cast<PyAsyncGenASend*>(&gen);
        PyAsyncGenObject* gen = asend->ags_gen;
        auto asend_yf = reinterpret_cast<PyObject*>(gen);
        auto result = GenInfo::create(asend_yf);
        recursion_depth--;
        return result;
    }

    if (!PyCoro_CheckExact(&gen) && !PyAsyncGen_CheckExact(&gen)) {
        recursion_depth--;
        return ErrorKind::GenInfoError;
    }

#if PY_VERSION_HEX >= 0x030b0000
    // The frame follows the generator object
    auto frame = (gen.gi_frame_state == FRAME_CLEARED)
                   ? NULL
                   : reinterpret_cast<PyObject*>(reinterpret_cast<char*>(gen_addr) + offsetof(PyGenObject, gi_iframe));
#else
    auto frame = (PyObject*)gen.gi_frame;
#endif

    PyFrameObject f;
    if (copy_type(frame, f)) {
        recursion_depth--;
        return ErrorKind::GenInfoError;
    }

    PyObject* yf = (frame != NULL ? PyGen_yf(&gen, frame) : NULL);
    GenInfo::Ptr await = nullptr;
    if (yf != NULL && yf != gen_addr) {
        auto maybe_await = GenInfo::create(yf);
        if (maybe_await) {
            await = std::move(*maybe_await);
        }
    }

#if PY_VERSION_HEX >= 0x030b0000
    auto is_running = (gen.gi_frame_state == FRAME_EXECUTING);
#elif PY_VERSION_HEX >= 0x030a0000
    auto is_running = (frame != NULL) ? _PyFrame_IsExecuting(&f) : false;
#else
    auto is_running = gen.gi_running;
#endif

    recursion_depth--;
    return std::make_unique<GenInfo>(gen_addr, frame, std::move(await), is_running);
}

// ----------------------------------------------------------------------------

class TaskInfo
{
  public:
    typedef std::unique_ptr<TaskInfo> Ptr;
    typedef std::reference_wrapper<TaskInfo> Ref;

    PyObject* origin = NULL;
    PyObject* loop = NULL;

    GenInfo::Ptr coro = nullptr;

    StringTable::Key name;

    // Information to reconstruct the async stack as best as we can
    TaskInfo::Ptr waiter = nullptr;

    [[nodiscard]] static Result<TaskInfo::Ptr> create(TaskObj*);
    TaskInfo(PyObject* origin, PyObject* loop, GenInfo::Ptr coro, StringTable::Key name, TaskInfo::Ptr waiter)
      : origin(origin)
      , loop(loop)
      , coro(std::move(coro))
      , name(name)
      , waiter(std::move(waiter))
    {
    }

    [[nodiscard]] static Result<TaskInfo::Ptr> current(PyObject*);
    inline size_t unwind(FrameStack&);
};

inline std::unordered_map<PyObject*, PyObject*> task_link_map;
inline std::mutex task_link_map_lock;

// ----------------------------------------------------------------------------
inline Result<TaskInfo::Ptr>
TaskInfo::create(TaskObj* task_addr)
{
    static thread_local size_t recursion_depth = 0;
    recursion_depth++;

    if (recursion_depth > MAX_RECURSION_DEPTH) {
        recursion_depth--;
        return ErrorKind::TaskInfoError;
    }

    TaskObj task;
    if (copy_type(task_addr, task)) {
        recursion_depth--;
        return ErrorKind::TaskInfoError;
    }

    auto maybe_coro = GenInfo::create(task.task_coro);
    if (!maybe_coro) {
        recursion_depth--;
        return ErrorKind::TaskInfoGeneratorError;
    }

    auto origin = reinterpret_cast<PyObject*>(task_addr);

    auto maybe_name = string_table.key(task.task_name);
    if (!maybe_name) {
        recursion_depth--;
        return ErrorKind::TaskInfoError;
    }

    auto name = *maybe_name;
    auto loop = task.task_loop;

    TaskInfo::Ptr waiter = nullptr;
    if (task.task_fut_waiter) {
        auto maybe_waiter = TaskInfo::create(reinterpret_cast<TaskObj*>(task.task_fut_waiter)); // TODO: Make lazy?
        if (maybe_waiter) {
            waiter = std::move(*maybe_waiter);
        }
    }

    recursion_depth--;
    return std::make_unique<TaskInfo>(origin, loop, std::move(*maybe_coro), name, std::move(waiter));
}

// ----------------------------------------------------------------------------
inline Result<TaskInfo::Ptr>
TaskInfo::current(PyObject* loop)
{
    if (loop == NULL) {
        return ErrorKind::TaskInfoError;
    }

    auto maybe_current_tasks_dict = MirrorDict::create(asyncio_current_tasks);
    if (!maybe_current_tasks_dict) {
        return ErrorKind::TaskInfoError;
    }

    auto current_tasks_dict = std::move(*maybe_current_tasks_dict);
    PyObject* task = current_tasks_dict.get_item(loop);
    if (task == NULL) {
        return ErrorKind::TaskInfoError;
    }

    return TaskInfo::create(reinterpret_cast<TaskObj*>(task));
}

// ----------------------------------------------------------------------------
// TODO: Make this a "for_each_task" function?
[[nodiscard]] inline Result<std::vector<TaskInfo::Ptr>>
get_all_tasks(PyObject* loop)
{
    std::vector<TaskInfo::Ptr> tasks;
    if (loop == NULL)
        return tasks;

    auto maybe_scheduled_tasks_set = MirrorSet::create(asyncio_scheduled_tasks);
    if (!maybe_scheduled_tasks_set) {
        return ErrorKind::TaskInfoError;
    }

    auto scheduled_tasks_set = std::move(*maybe_scheduled_tasks_set);
    auto maybe_scheduled_tasks = scheduled_tasks_set.as_unordered_set();
    if (!maybe_scheduled_tasks) {
        return ErrorKind::TaskInfoError;
    }

    auto scheduled_tasks = std::move(*maybe_scheduled_tasks);
    for (auto task_wr_addr : scheduled_tasks) {
        PyWeakReference task_wr;
        if (copy_type(task_wr_addr, task_wr))
            continue;

        auto maybe_task_info = TaskInfo::create(reinterpret_cast<TaskObj*>(task_wr.wr_object));
        if (maybe_task_info) {
            if ((*maybe_task_info)->loop == loop) {
                tasks.push_back(std::move(*maybe_task_info));
            }
        }
    }

    if (asyncio_eager_tasks != NULL) {
        auto maybe_eager_tasks_set = MirrorSet::create(asyncio_eager_tasks);
        if (!maybe_eager_tasks_set) {
            return ErrorKind::TaskInfoError;
        }

        auto eager_tasks_set = std::move(*maybe_eager_tasks_set);

        auto maybe_eager_tasks = eager_tasks_set.as_unordered_set();
        if (!maybe_eager_tasks) {
            return ErrorKind::TaskInfoError;
        }

        auto eager_tasks = std::move(*maybe_eager_tasks);
        for (auto task_addr : eager_tasks) {
            auto maybe_task_info = TaskInfo::create(reinterpret_cast<TaskObj*>(task_addr));
            if (maybe_task_info) {
                if ((*maybe_task_info)->loop == loop) {
                    tasks.push_back(std::move(*maybe_task_info));
                }
            }
        }
    }

    return tasks;
}

// ----------------------------------------------------------------------------

inline std::vector<std::unique_ptr<StackInfo>> current_tasks;

// ----------------------------------------------------------------------------

inline size_t
TaskInfo::unwind(FrameStack& stack)
{
    // TODO: Check for running task.
    std::stack<PyObject*> coro_frames;

    // Unwind the coro chain
    for (auto py_coro = this->coro.get(); py_coro != NULL; py_coro = py_coro->await.get()) {
        if (py_coro->frame != NULL)
            coro_frames.push(py_coro->frame);
    }

    int count = 0;

    // Unwind the coro frames
    while (!coro_frames.empty()) {
        PyObject* frame = coro_frames.top();
        coro_frames.pop();

        count += unwind_frame(frame, stack);
    }

    return count;
}
