#include <echion/echion_sampler.h>
#include <echion/tasks.h>

#include <echion/echion_sampler.h>

#include <stack>
#include <vector>

Result<GenInfo::Ptr>
GenInfo::create(PyObject* gen_addr)
{
    return create_impl(gen_addr, 0);
}

Result<GenInfo::Ptr>
GenInfo::create_impl(PyObject* gen_addr, size_t recursion_depth)
{
    if (recursion_depth > MAX_RECURSION_DEPTH) {
        return ErrorKind::GenInfoError;
    }

    PyGenObject gen;
    if (copy_type(gen_addr, gen)) {
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
        return GenInfo::create_impl(asend_yf, recursion_depth + 1);
    }

    if (!PyCoro_CheckExact(&gen) && !PyAsyncGen_CheckExact(&gen)) {
        return ErrorKind::GenInfoError;
    }

#if PY_VERSION_HEX >= 0x030b0000
    // The frame follows the generator object
    auto gen_frame =
      (gen.gi_frame_state == FRAME_CLEARED)
        ? NULL
        : reinterpret_cast<PyObject*>(reinterpret_cast<char*>(gen_addr) + offsetof(PyGenObject, gi_iframe));
#else
    auto gen_frame = reinterpret_cast<PyObject*>(gen.gi_frame);
#endif

    PyFrameObject f;
    if (copy_type(gen_frame, f)) {
        return ErrorKind::GenInfoError;
    }

    PyObject* yf = (gen_frame != NULL ? PyGen_yf(&gen, gen_frame) : NULL);
    GenInfo::Ptr gen_await = nullptr;
    if (yf != NULL && yf != gen_addr) {
        auto maybe_await = GenInfo::create_impl(yf, recursion_depth + 1);
        if (maybe_await) {
            gen_await = std::move(*maybe_await);
        }
    }

    // A coroutine awaiting another coroutine is never running itself,
    // so when the coroutine is awaiting another coroutine, we use the running state of the awaited coroutine.
    // Otherwise, we use the running state of the coroutine itself.
    bool gen_is_running = false;
    if (gen_await) {
        gen_is_running = gen_await->is_running;
    } else {
#if PY_VERSION_HEX >= 0x030b0000
        gen_is_running = (gen.gi_frame_state == FRAME_EXECUTING);
#elif PY_VERSION_HEX >= 0x030a0000
        gen_is_running = (gen_frame != NULL) ? _PyFrame_IsExecuting(&f) : false;
#else
        gen_is_running = gen.gi_running;
#endif
    }

    return std::make_unique<GenInfo>(gen_addr, gen_frame, std::move(gen_await), gen_is_running);
}

// ----------------------------------------------------------------------------
Result<TaskInfo::Ptr>
TaskInfo::create(EchionSampler& echion, TaskObj* task_addr)
{
    return create_impl(echion, task_addr, 0);
}

Result<TaskInfo::Ptr>
TaskInfo::create_impl(EchionSampler& echion, TaskObj* task_addr, size_t recursion_depth)
{
    if (recursion_depth > MAX_RECURSION_DEPTH) {
        return ErrorKind::TaskInfoError;
    }

    TaskObj task;
    if (copy_type(task_addr, task)) {
        return ErrorKind::TaskInfoError;
    }

    auto maybe_coro = GenInfo::create(task.task_coro);
    if (!maybe_coro) {
        return ErrorKind::TaskInfoGeneratorError;
    }

    auto maybe_name = echion.string_table().key(task.task_name, StringTag::TaskName);
    if (!maybe_name) {
        return ErrorKind::TaskInfoError;
    }

    auto task_name = *maybe_name;
    auto task_loop = task.task_loop;

    TaskInfo::Ptr task_waiter = nullptr;
    if (task.task_fut_waiter) {
        auto maybe_waiter =
          TaskInfo::create_impl(echion, reinterpret_cast<TaskObj*>(task.task_fut_waiter), recursion_depth + 1);
        if (maybe_waiter) {
            task_waiter = std::move(*maybe_waiter);
        }
    }

    return std::make_unique<TaskInfo>(
      reinterpret_cast<PyObject*>(task_addr), task_loop, std::move(*maybe_coro), task_name, std::move(task_waiter));
}

// When uvloop.run() is used, the top-level Task contains a wrapper coroutine
// named "run.<locals>.wrapper" that just validates the loop type and awaits the user's main
// coroutine. We skip this frame to keep the stack clean and consistent with regular asyncio.
bool
is_uvloop_wrapper_frame(EchionSampler& echion, bool using_uvloop, const Frame& frame)
{
    if (!using_uvloop) {
        return false;
    }

    const auto& frame_name = echion.string_table().lookup(frame.name)->get();

#if PY_VERSION_HEX >= 0x030b0000
    // Python 3.11+: qualified name includes the enclosing function
    constexpr std::string_view wrapper_name = "run.<locals>.wrapper";
    return frame_name == wrapper_name;
#else
    // Python < 3.11: just check for "wrapper" in uvloop/__init__.py
    constexpr std::string_view uvloop_init_py = "uvloop/__init__.py";
    constexpr std::string_view wrapper = "wrapper";
    auto filename = echion.string_table().lookup(frame.filename)->get();
    auto is_uvloop = filename.rfind(uvloop_init_py) == filename.size() - uvloop_init_py.size();
    return is_uvloop && (frame_name == wrapper);
#endif
}

size_t
TaskInfo::unwind(EchionSampler& echion, FrameStack& stack, bool using_uvloop)
{
    // TODO: Check for running task.
    (void)echion;

    // Use a vector-based std::stack as we only push_back/pop_back
    std::stack<PyObject*, std::vector<PyObject*>> coro_frames;

    // Unwind the coro chain
    size_t itr_count = 0;
    for (auto py_coro = this->coro.get(); py_coro != NULL; py_coro = py_coro->await.get()) {
        if (py_coro->frame != NULL) {
            coro_frames.push(py_coro->frame);
            itr_count++;
            if (itr_count >= MAX_RECURSION_DEPTH) {
                break;
            }
        }
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
        auto new_frames = unwind_frame(echion, frame, stack, 1);
        assert(new_frames <= 1 && "expected exactly 1 frame to be unwound (or 0 in case of an error)");

        // If we failed to unwind the Frame, stop unwinding the coroutine chain; otherwise we could
        // end up with Stacks with missing Frames between two coroutines Frames.
        if (new_frames == 0) {
            break;
        }

        // Skip the uvloop wrapper frame if present (only at the outermost level of the top-level Task)
        if (!stack.empty() && is_uvloop_wrapper_frame(echion, using_uvloop, stack.back().get())) {
            stack.pop_back();
            continue;
        }

        count += 1;
    }

    return count;
}
