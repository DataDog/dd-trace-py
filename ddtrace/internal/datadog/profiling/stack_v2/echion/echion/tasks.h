// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#include <optional>

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <frameobject.h>
#include <weakrefobject.h>

#if PY_VERSION_HEX >= 0x030b0000
#include <cpython/genobject.h>

#define Py_BUILD_CORE
#include <cstddef>
#if PY_VERSION_HEX >= 0x030e0000
#include <internal/pycore_frame.h>
#include <internal/pycore_interp_structs.h>
#include <internal/pycore_llist.h>
#include <internal/pycore_tstate.h>
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

    PyObject* origin = nullptr;
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
        PyAsyncGenObject* gen_ptr = asend->ags_gen;
        auto asend_yf = reinterpret_cast<PyObject*>(gen_ptr);
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

    // A coroutine awaiting another coroutine is never running itself,
    // so when the coroutine is awaiting another coroutine, we use the running state of the awaited coroutine.
    // Otherwise, we use the running state of the coroutine itself.
    bool is_running = false;
    if (await) {
        is_running = await->is_running;
    } else {
#if PY_VERSION_HEX >= 0x030b0000
        is_running = (gen.gi_frame_state == FRAME_EXECUTING);
#elif PY_VERSION_HEX >= 0x030a0000
        is_running = (frame != NULL) ? _PyFrame_IsExecuting(&f) : false;
#else
        is_running = gen.gi_running;
#endif
    }

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
    bool is_on_cpu = false;

    [[nodiscard]] static Result<TaskInfo::Ptr> create(TaskObj*);
    TaskInfo(PyObject* origin, PyObject* loop, GenInfo::Ptr coro, StringTable::Key name, TaskInfo::Ptr waiter)
      : origin(origin)
      , loop(loop)
      , coro(std::move(coro))
      , name(name)
      , waiter(std::move(waiter))
      , is_on_cpu(this->coro && this->coro->is_running)
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
    return std::make_unique<TaskInfo>(
      reinterpret_cast<PyObject*>(task_addr), loop, std::move(*maybe_coro), name, std::move(waiter));
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
#if PY_VERSION_HEX >= 0x030e0000
// Python 3.14+: Get tasks from a single thread's linked-list
[[nodiscard]] inline Result<void>
get_tasks_from_linked_list(uintptr_t head_addr, PyObject* loop, std::vector<TaskInfo::Ptr>& tasks)
{
    if (head_addr == 0 || loop == nullptr) {
        return ErrorKind::TaskInfoError;
    }

    // Copy head node struct from remote memory to local memory
    struct llist_node head_node_local;
    if (copy_type(reinterpret_cast<void*>(head_addr), head_node_local)) {
        return ErrorKind::TaskInfoError;
    }

    // Check if list is empty (head points to itself in circular list)
    uintptr_t head_addr_uint = head_addr;
    uintptr_t next_as_uint = reinterpret_cast<uintptr_t>(head_node_local.next);
    uintptr_t prev_as_uint = reinterpret_cast<uintptr_t>(head_node_local.prev);
    if (next_as_uint == head_addr_uint && prev_as_uint == head_addr_uint) {
        return Result<void>::ok();
    }

    struct llist_node current_node = head_node_local; // Start with head node
    uintptr_t current_node_addr = head_addr;          // Address of current node

    // Copied from CPython's _remote_debugging_module.c: MAX_ITERATIONS
    const size_t MAX_ITERATIONS = 1 << 16;
    size_t iteration_count = 0;

    // Iterate over linked-list. The linked list is circular, so we stop
    // when we're back at head.
    while (reinterpret_cast<uintptr_t>(current_node.next) != head_addr_uint) {
        // Safety: prevent infinite loops
        if (++iteration_count > MAX_ITERATIONS) {
            return ErrorKind::TaskInfoError;
        }

        if (current_node.next == nullptr) {
            return ErrorKind::TaskInfoError; // nullptr pointer - invalid list
        }

        uintptr_t next_node_addr = reinterpret_cast<uintptr_t>(current_node.next);

        // Calculate task_addr from current_node.next
        size_t task_node_offset_val = offsetof(TaskObj, task_node);
        uintptr_t task_addr_uint = next_node_addr - task_node_offset_val;

        // Create TaskInfo for the task
        auto maybe_task_info = TaskInfo::create(reinterpret_cast<TaskObj*>(task_addr_uint));
        if (maybe_task_info) {
            auto& task_info = *maybe_task_info;
            if (task_info->loop == loop) {
                tasks.push_back(std::move(task_info));
            }
        }

        // Read next node from current_node.next into current_node
        if (copy_type(reinterpret_cast<void*>(next_node_addr), current_node)) {
            return ErrorKind::TaskInfoError; // Failed to read next node
        }
        current_node_addr = next_node_addr; // Update address for next iteration
    }

    return Result<void>::ok();
}

// Get tasks from thread's linked-list (for active tasks)
// NOTE: This function uses an output parameter instead of returning Result<std::vector<TaskInfo::Ptr>>
// for performance reasons. When accumulating tasks from multiple sources (thread list, interpreter list,
// scheduled tasks), using output parameters allows direct appending to a single vector, avoiding the
// overhead of moving/copying elements between intermediate vectors.
[[nodiscard]] inline Result<void>
get_tasks_from_thread_linked_list(_PyThreadStateImpl* tstate_impl, PyObject* loop, std::vector<TaskInfo::Ptr>& tasks)
{
    if (tstate_impl == nullptr || loop == nullptr) {
        return ErrorKind::TaskInfoError;
    }

    uintptr_t head_addr = reinterpret_cast<uintptr_t>(&tstate_impl->asyncio_tasks_head);

    return get_tasks_from_linked_list(head_addr, loop, tasks);
}

// Get tasks from interpreter's linked-list (for lingering tasks)
// NOTE: This function uses an output parameter instead of returning Result<std::vector<TaskInfo::Ptr>>
// for performance reasons. When accumulating tasks from multiple sources (thread list, interpreter list,
// scheduled tasks), using output parameters allows direct appending to a single vector, avoiding the
// overhead of moving/copying elements between intermediate vectors.
[[nodiscard]] inline Result<void>
get_tasks_from_interpreter_linked_list(PyThreadState* tstate, PyObject* loop, std::vector<TaskInfo::Ptr>& tasks)
{
    if (tstate == nullptr || loop == nullptr) {
        return ErrorKind::TaskInfoError;
    }

    // Step 1: Get interpreter state from thread state
    // tstate->interp points to PyInterpreterState
    PyInterpreterState interp;
    if (copy_type(tstate->interp, interp)) {
        return ErrorKind::TaskInfoError;
    }

    // Step 2: Calculate interpreter's asyncio_tasks_head address
    uintptr_t interp_addr = reinterpret_cast<uintptr_t>(tstate->interp);
    constexpr size_t asyncio_tasks_head_offset = offsetof(PyInterpreterState, asyncio_tasks_head);
    uintptr_t head_addr = interp_addr + asyncio_tasks_head_offset;

    // Step 3: Call the shared linked-list iteration function
    return get_tasks_from_linked_list(head_addr, loop, tasks);
}
#endif

// ----------------------------------------------------------------------------
// TODO: Make this a "for_each_task" function?
#if PY_VERSION_HEX >= 0x030e0000
[[nodiscard]] inline Result<std::vector<TaskInfo::Ptr>>
get_all_tasks(PyObject* loop, _PyThreadStateImpl* tstate_impl = nullptr)
{
    std::vector<TaskInfo::Ptr> tasks;
    if (loop == NULL)
        return tasks;

    // Python 3.14+: Native tasks are in linked-list per thread AND per interpreter
    // CPython iterates over both:
    // 1. Per-thread list: tstate_impl->asyncio_tasks_head (active tasks)
    // 2. Per-interpreter list: interp->asyncio_tasks_head (lingering tasks)
    // First, get tasks from this thread's linked-list (if tstate_impl is provided)
    // Note: We continue processing even if one source fails to maximize partial results
    if (tstate_impl != nullptr) {
        (void)get_tasks_from_thread_linked_list(tstate_impl, loop, tasks);

        // Second, get tasks from interpreter's linked-list (lingering tasks)
        // Access PyThreadState via the first field of _PyThreadStateImpl
        PyThreadState* tstate = reinterpret_cast<PyThreadState*>(tstate_impl);
        (void)get_tasks_from_interpreter_linked_list(tstate, loop, tasks);
    }

    // Handle third-party tasks from Python _scheduled_tasks WeakSet
    // In Python 3.14+, _scheduled_tasks is a Python-level weakref.WeakSet() that only contains
    // tasks that don't inherit from asyncio.Task. Native asyncio.Task instances are stored
    // in linked-lists (handled above) and are NOT added to _scheduled_tasks.
    // This is typically empty in practice, but we handle it for completeness.
    if (asyncio_scheduled_tasks != nullptr) {
        if (auto maybe_scheduled_tasks_set = MirrorSet::create(asyncio_scheduled_tasks)) {
            auto scheduled_tasks_set = std::move(*maybe_scheduled_tasks_set);
            if (auto maybe_scheduled_tasks = scheduled_tasks_set.as_unordered_set()) {
                auto scheduled_tasks = std::move(*maybe_scheduled_tasks);
                for (auto task_addr : scheduled_tasks) {
                    // In WeakSet.data (set), elements are the Task objects themselves
                    auto maybe_task_info = TaskInfo::create(reinterpret_cast<TaskObj*>(task_addr));
                    if (maybe_task_info && (*maybe_task_info)->loop == loop) {
                        tasks.push_back(std::move(*maybe_task_info));
                    }
                }
            }
        }
    }
#else
[[nodiscard]] inline Result<std::vector<TaskInfo::Ptr>>
get_all_tasks(PyObject* loop, PyThreadState* tstate = nullptr)
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
#endif

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
