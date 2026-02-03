#include <echion/tasks.h>

#include <echion/echion_sampler.h>

#include <stack>

Result<GenInfo::Ptr>
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
    auto frame = reinterpret_cast<PyObject*>(gen.gi_frame);
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
Result<TaskInfo::Ptr>
TaskInfo::create(TaskObj* task_addr, StringTable& string_table)
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

    auto maybe_name = string_table.key(task.task_name, StringTag::TaskName);
    if (!maybe_name) {
        recursion_depth--;
        return ErrorKind::TaskInfoError;
    }

    auto name = *maybe_name;
    auto loop = task.task_loop;

    TaskInfo::Ptr waiter = nullptr;
    if (task.task_fut_waiter) {
        auto maybe_waiter =
          TaskInfo::create(reinterpret_cast<TaskObj*>(task.task_fut_waiter), string_table); // TODO: Make lazy?
        if (maybe_waiter) {
            waiter = std::move(*maybe_waiter);
        }
    }

    recursion_depth--;
    return std::make_unique<TaskInfo>(
      reinterpret_cast<PyObject*>(task_addr), loop, std::move(*maybe_coro), name, std::move(waiter));
}

size_t
TaskInfo::unwind(FrameStack& stack, EchionSampler& echion)
{
    // TODO: Check for running task.

    // Use a vector-based std::stack as we only push_back/pop_back
    std::stack<PyObject*, std::vector<PyObject*>> coro_frames;

    // Unwind the coro chain
    for (auto py_coro = this->coro.get(); py_coro != NULL; py_coro = py_coro->await.get()) {
        if (py_coro->frame != NULL)
            coro_frames.push(py_coro->frame);
    }

    // Total number of frames added to the Stack
    size_t count = 0;

    // Unwind the coro frames
    while (!coro_frames.empty()) {
        PyObject* frame = coro_frames.top();
        coro_frames.pop();

        // We only need the single Task frame from each coroutine, not the full Python stack (which we already have
        // from the Thread Stack).
        // For a running Task, unwind_frame would also yield the asyncio runtime frames "on top"
        // of the Task frame, but we would discard those anyway. Limiting to 1 frame avoids walking
        // the Frame chain unnecessarily.
        auto new_frames = unwind_frame(frame, stack, echion, 1);
        assert(new_frames <= 1 && "expected exactly 1 frame to be unwound (or 0 in case of an error)");

        // If we failed to unwind the Frame, stop unwinding the coroutine chain; otherwise we could
        // end up with Stacks with missing Frames between two coroutines Frames.
        if (new_frames == 0) {
            break;
        }
        count += 1;
    }

    return count;
}
