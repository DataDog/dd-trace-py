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
#if PY_VERSION_HEX >= 0x030e0000
#include <cstddef>                          // For offsetof macro
#include <internal/pycore_frame.h>          // for FRAME_CLEARED
#include <internal/pycore_interp_structs.h> // For PyInterpreterState
#include <internal/pycore_llist.h>          // For llist_node structure
#include <opcode.h>
// Note: _PyThreadStateImpl is already available via echion/state.h which includes
// <internal/pycore_pystate.h> with Py_BUILD_CORE defined.
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

#if PY_VERSION_HEX >= 0x030a0000 && PY_VERSION_HEX < 0x030b0000
    // Python 3.10: Need PyFrameObject for _PyFrame_IsExecuting
    PyFrameObject f;
    if (copy_type(frame, f)) {
        recursion_depth--;
        return ErrorKind::GenInfoError;
    }
#endif

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
inline void
get_tasks_from_linked_list(uintptr_t head_addr, PyObject* loop, std::vector<TaskInfo::Ptr>& tasks)
{
    if (head_addr == 0 || loop == nullptr) {
        return;
    }

    // Copy head node struct from remote memory to local memory
    struct llist_node head_node_local;
    if (copy_type(reinterpret_cast<void*>(head_addr), head_node_local)) {
        return;
    }

    // Check if list is empty (head points to itself in circular list)
    uintptr_t head_addr_uint = head_addr;
    uintptr_t next_as_uint = reinterpret_cast<uintptr_t>(head_node_local.next);
    uintptr_t prev_as_uint = reinterpret_cast<uintptr_t>(head_node_local.prev);
    if (next_as_uint == head_addr_uint && prev_as_uint == head_addr_uint) {
        return;
    }

    struct llist_node current_node = head_node_local; // Start with head node
    uintptr_t current_node_addr = head_addr;          // Address of current node

    // Copied from CPython's _remote_debugging_module.c: MAX_ITERATIONS
    const size_t MAX_ITERATIONS = 2 << 15;
    size_t iteration_count = 0;

    // Iterate over linked-list. The linked list is circular, so we stop
    // when we're back at head.
    while (reinterpret_cast<uintptr_t>(current_node.next) != head_addr_uint) {
        // Safety: prevent infinite loops
        if (++iteration_count > MAX_ITERATIONS) {
            return;
        }

        if (current_node.next == nullptr) {
            return; // nullptr pointer - invalid list
        }

        uintptr_t next_node_addr = reinterpret_cast<uintptr_t>(current_node.next);

        // Calculate task_addr from current_node.next
        size_t task_node_offset_val = offsetof(TaskObj, task_node);
        uintptr_t task_addr_uint = next_node_addr - task_node_offset_val;

        // Create TaskInfo for the task
        auto maybe_task_info = TaskInfo::create(reinterpret_cast<TaskObj*>(task_addr_uint));
        if (maybe_task_info) {
            if ((*maybe_task_info)->loop == loop) {
                tasks.push_back(std::move(*maybe_task_info));
            }
        }

        // Read next node from current_node.next into current_node
        if (copy_type(reinterpret_cast<void*>(next_node_addr), current_node)) {
            return; // Failed to read next node
        }
        current_node_addr = next_node_addr; // Update address for next iteration
    }
}

inline void
get_tasks_from_thread_linked_list(uintptr_t tstate_addr, PyObject* loop, std::vector<TaskInfo::Ptr>& tasks)
{
    if (tstate_addr == 0 || loop == nullptr) {
        return;
    }

    // Calculate offset to asyncio_tasks_head field
    // NOTE: tstate_addr points to PyThreadState base, which is the first field of _PyThreadStateImpl
    size_t asyncio_tasks_head_offset = offsetof(_PyThreadStateImpl, asyncio_tasks_head);
    uintptr_t head_addr = tstate_addr + asyncio_tasks_head_offset;

    // Copy the llist_node struct from remote memory to local memory
    struct llist_node head_node_local;
    if (copy_type(reinterpret_cast<void*>(head_addr), head_node_local)) {
        return; // Failed to read head from remote memory
    }

    // Check if list is empty (head points to itself in circular list)
    uintptr_t next_as_uint = reinterpret_cast<uintptr_t>(head_node_local.next);
    uintptr_t prev_as_uint = reinterpret_cast<uintptr_t>(head_node_local.prev);
    if (next_as_uint == head_addr && prev_as_uint == head_addr) {
        return; // Empty list
    }

    // Iterate over the linked-list
    get_tasks_from_linked_list(head_addr, loop, tasks);
}

// CRITICAL: All memory access must copy structs to local memory first!
// Get tasks from interpreter's linked-list (for lingering tasks)
inline void
get_tasks_from_interpreter_linked_list(PyThreadState* tstate, PyObject* loop, std::vector<TaskInfo::Ptr>& tasks)
{
    if (tstate == nullptr || loop == nullptr) {
        return;
    }

    // Step 1: Get interpreter state from thread state
    // tstate->interp points to PyInterpreterState
    PyInterpreterState interp;
    if (copy_type(tstate->interp, interp)) {
        return;
    }

    // Step 2: Calculate interpreter's asyncio_tasks_head address
    uintptr_t interp_addr = reinterpret_cast<uintptr_t>(tstate->interp);
    size_t asyncio_tasks_head_offset = offsetof(PyInterpreterState, asyncio_tasks_head);
    uintptr_t head_addr = interp_addr + asyncio_tasks_head_offset;

    // Step 3: Call the shared linked-list iteration function
    get_tasks_from_linked_list(head_addr, loop, tasks);
}
#endif

// ----------------------------------------------------------------------------
// TODO: Make this a "for_each_task" function?
[[nodiscard]] inline Result<std::vector<TaskInfo::Ptr>>
get_all_tasks(PyObject* loop, PyThreadState* tstate = nullptr, uintptr_t tstate_addr = 0)
{
    std::vector<TaskInfo::Ptr> tasks;
    if (loop == NULL)
        return tasks;

#if PY_VERSION_HEX >= 0x030e0000
    // Python 3.14+: Native tasks are in linked-list per thread AND per interpreter
    // CPython iterates over both:
    // 1. Per-thread list: tstate->asyncio_tasks_head (active tasks)
    // 2. Per-interpreter list: interp->asyncio_tasks_head (lingering tasks)
    // First, get tasks from this thread's linked-list (if tstate_addr is provided)
    if (tstate_addr != 0) {
        get_tasks_from_thread_linked_list(tstate_addr, loop, tasks);
    }

    // Second, get tasks from interpreter's linked-list (lingering tasks)
    // This needs tstate to dereference tstate->interp
    if (tstate != nullptr) {
        get_tasks_from_interpreter_linked_list(tstate, loop, tasks);
    }

    // Handle third-party tasks from Python _scheduled_tasks.data (set)
    // (asyncio_scheduled_tasks is now WeakSet.data, which is a Python set)
    // These are global, not per-thread, so we collect them once
    // If MirrorSet::create() fails, the set might be empty or invalid - skip it
    if (asyncio_scheduled_tasks == nullptr) {
        // Skip if not initialized
    } else if (auto maybe_scheduled_tasks_set = MirrorSet::create(asyncio_scheduled_tasks)) {
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
#else
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
        if (maybe_eager_tasks_set) {
            auto eager_tasks_set = std::move(*maybe_eager_tasks_set);
            auto maybe_eager_tasks = eager_tasks_set.as_unordered_set();
            if (maybe_eager_tasks) {
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
        }
        // If MirrorSet::create() fails, the set might be empty or invalid - skip it
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
