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
#endif  // PY_VERSION_HEX >= 0x030d0000
#else
#include <genobject.h>
#include <opcode.h>
#endif  // PY_VERSION_HEX >= 0x30b0000

#include <exception>
#include <mutex>
#include <stack>
#include <unordered_map>
#include <vector>

#include <echion/config.h>
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

class GenInfo
{
public:
    typedef std::unique_ptr<GenInfo> Ptr;

    class Error : public std::exception
    {
    public:
        const char* what() const noexcept override
        {
            return "Cannot create generator info object";
        }
    };

    PyObject* origin = NULL;
    PyObject* frame = NULL;

    GenInfo::Ptr await = nullptr;

    bool is_running = false;

    GenInfo(PyObject* gen_addr);
};

inline GenInfo::GenInfo(PyObject* gen_addr)
{
    static thread_local size_t recursion_depth = 0;
    recursion_depth++;

    if (recursion_depth > MAX_RECURSION_DEPTH) {
        recursion_depth--;
        throw Error();
    }

    PyGenObject gen;

    if (copy_type(gen_addr, gen) || !PyCoro_CheckExact(&gen)) {
        recursion_depth--;
        throw Error();
    }

    origin = gen_addr;

#if PY_VERSION_HEX >= 0x030b0000
    // The frame follows the generator object
    frame = (gen.gi_frame_state == FRAME_CLEARED)
                ? NULL
                : (PyObject*)((char*)gen_addr + offsetof(PyGenObject, gi_iframe));
#else
    frame = (PyObject*)gen.gi_frame;
#endif

    PyFrameObject f;
    if (copy_type(frame, f)) {
        recursion_depth--;
        throw Error();
    }

    PyObject* yf = (frame != NULL ? PyGen_yf(&gen, frame) : NULL);
    if (yf != NULL && yf != gen_addr)
    {
        try
        {
            await = std::make_unique<GenInfo>(yf);
        }
        catch (GenInfo::Error&)
        {
            await = nullptr;
        }
    }

#if PY_VERSION_HEX >= 0x030b0000
    is_running = (gen.gi_frame_state == FRAME_EXECUTING);
#elif PY_VERSION_HEX >= 0x030a0000
    is_running = (frame != NULL) ? _PyFrame_IsExecuting(&f) : false;
#else
    is_running = gen.gi_running;
#endif

    recursion_depth--;
}

// ----------------------------------------------------------------------------

class TaskInfo
{
public:
    typedef std::unique_ptr<TaskInfo> Ptr;
    typedef std::reference_wrapper<TaskInfo> Ref;

    class Error : public std::exception
    {
    public:
        const char* what() const noexcept override
        {
            return "Cannot create task info object";
        }
    };

    class GeneratorError : public Error
    {
    public:
        const char* what() const noexcept override
        {
            return "Cannot create generator info object";
        }
    };

    PyObject* origin = NULL;
    PyObject* loop = NULL;

    GenInfo::Ptr coro = nullptr;

    StringTable::Key name;

    // Information to reconstruct the async stack as best as we can
    TaskInfo::Ptr waiter = nullptr;

    TaskInfo(TaskObj*);

    static TaskInfo current(PyObject*);
    inline size_t unwind(FrameStack&);
};

inline std::unordered_map<PyObject*, PyObject*> task_link_map;
inline std::mutex task_link_map_lock;

// ----------------------------------------------------------------------------
inline TaskInfo::TaskInfo(TaskObj* task_addr)
{
    static thread_local size_t recursion_depth = 0;
    recursion_depth++;

    if (recursion_depth > MAX_RECURSION_DEPTH) {
        recursion_depth--;
        throw Error();
    }

    TaskObj task;
    if (copy_type(task_addr, task)) {
        recursion_depth--;
        throw Error();
    }

    try
    {
        coro = std::make_unique<GenInfo>(task.task_coro);
    }
    catch (GenInfo::Error&)
    {
        recursion_depth--;
        throw GeneratorError();
    }

    origin = (PyObject*)task_addr;

    try
    {
        name = string_table.key(task.task_name);
    }
    catch (StringTable::Error&)
    {
        recursion_depth--;
        throw Error();
    }

    loop = task.task_loop;

    if (task.task_fut_waiter)
    {
        try
        {
            waiter =
                std::make_unique<TaskInfo>((TaskObj*)task.task_fut_waiter);  // TODO: Make lazy?
        }
        catch (TaskInfo::Error&)
        {
            waiter = nullptr;
        }
    }

    recursion_depth--;
}

// ----------------------------------------------------------------------------
inline TaskInfo TaskInfo::current(PyObject* loop)
{
    if (loop == NULL)
        throw Error();

    try
    {
        MirrorDict current_tasks_dict(asyncio_current_tasks);
        PyObject* task = current_tasks_dict.get_item(loop);
        if (task == NULL)
            throw Error();

        return TaskInfo((TaskObj*)task);
    }
    catch (MirrorError& e)
    {
        throw Error();
    }
}

// ----------------------------------------------------------------------------
// TODO: Make this a "for_each_task" function?
inline std::vector<TaskInfo::Ptr> get_all_tasks(PyObject* loop)
{
    std::vector<TaskInfo::Ptr> tasks;
    if (loop == NULL)
        return tasks;

    try
    {
        MirrorSet scheduled_tasks_set(asyncio_scheduled_tasks);
        auto scheduled_tasks = scheduled_tasks_set.as_unordered_set();

        for (auto task_wr_addr : scheduled_tasks)
        {
            PyWeakReference task_wr;
            if (copy_type(task_wr_addr, task_wr))
                continue;

            try
            {
                auto task_info = std::make_unique<TaskInfo>((TaskObj*)task_wr.wr_object);
                if (task_info->loop == loop)
                    tasks.push_back(std::move(task_info));
            }
            catch (TaskInfo::Error& e)
            {
                // We failed to get this task but we keep going
            }
        }

        if (asyncio_eager_tasks != NULL)
        {
            MirrorSet eager_tasks_set(asyncio_eager_tasks);
            auto eager_tasks = eager_tasks_set.as_unordered_set();

            for (auto task_addr : eager_tasks)
            {
                try
                {
                    auto task_info = std::make_unique<TaskInfo>((TaskObj*)task_addr);
                    if (task_info->loop == loop)
                        tasks.push_back(std::move(task_info));
                }
                catch (TaskInfo::Error& e)
                {
                    // We failed to get this task but we keep going
                }
            }
        }

        return tasks;
    }
    catch (MirrorError& e)
    {
        throw TaskInfo::Error();
    }
}

// ----------------------------------------------------------------------------

inline std::vector<std::unique_ptr<StackInfo>> current_tasks;

// ----------------------------------------------------------------------------

inline size_t TaskInfo::unwind(FrameStack& stack)
{
    // TODO: Check for running task.
    std::stack<PyObject*> coro_frames;

    // Unwind the coro chain
    for (auto coro = this->coro.get(); coro != NULL; coro = coro->await.get())
    {
        if (coro->frame != NULL)
            coro_frames.push(coro->frame);
    }

    int count = 0;

    // Unwind the coro frames
    while (!coro_frames.empty())
    {
        PyObject* frame = coro_frames.top();
        coro_frames.pop();

        count += unwind_frame(frame, stack);
    }

    return count;
}
