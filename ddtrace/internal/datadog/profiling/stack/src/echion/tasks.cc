#include <echion/echion_sampler.h>
#include <echion/tasks.h>

#include <echion/echion_sampler.h>

#include <stack>
#include <vector>

Result<GenInfo::Ptr>
GenInfo::create(PyObject* gen_addr)
{
    return create_impl(gen_addr);
}

Result<GenInfo::Ptr>
GenInfo::create_impl(PyObject* gen_addr)
{
    // Each node records the coroutine address, its frame address, and the fields
    // needed to compute is_running for the leaf of the await chain.
    struct Node
    {
        PyObject* origin;
        PyObject* frame;
#if PY_VERSION_HEX >= 0x030b0000
        int8_t gi_frame_state;
#elif PY_VERSION_HEX >= 0x030a0000
        bool frame_is_executing;
#else
        int gi_running;
#endif
    };
    static_assert(sizeof(Node) * MAX_RECURSION_DEPTH <= static_cast<size_t>(10) * 1024,
                  "Node array too large (max 10KB)");
    std::array<Node, MAX_RECURSION_DEPTH> chain;

    PyObject* cur = gen_addr;
    for (size_t depth = 0; depth <= MAX_RECURSION_DEPTH; ++depth) {
        PyGenObject gen;
        if (copy_type(cur, gen)) {
            break;
        }

        // PyAsyncGenASend is a transparent wrapper: follow its ags_gen pointer
        // without adding a node to the chain.
        if (PyAsyncGenASend_CheckExact(&gen)) {
            static_assert(
              sizeof(PyAsyncGenASend) <= sizeof(PyGenObject),
              "PyAsyncGenASend must be smaller than PyGenObject in order for copy_type to have copied enough data.");
            PyAsyncGenASend* asend = reinterpret_cast<PyAsyncGenASend*>(&gen);
            cur = reinterpret_cast<PyObject*>(asend->ags_gen);
            continue;
        }

        if (!PyCoro_CheckExact(&gen) && !PyAsyncGen_CheckExact(&gen)) {
            break;
        }

#if PY_VERSION_HEX >= 0x030b0000
        auto gen_frame =
          (gen.gi_frame_state == FRAME_CLEARED)
            ? NULL
            : reinterpret_cast<PyObject*>(reinterpret_cast<char*>(cur) + offsetof(PyGenObject, gi_iframe));
#else
        auto gen_frame = reinterpret_cast<PyObject*>(gen.gi_frame);
#endif

        PyFrameObject f;
        if (copy_type(gen_frame, f)) {
            break;
        }

        PyObject* yf = (gen_frame != NULL ? PyGen_yf(&gen, gen_frame) : NULL);

        Node node;
        node.origin = cur;
        node.frame = gen_frame;
#if PY_VERSION_HEX >= 0x030b0000
        node.gi_frame_state = gen.gi_frame_state;
#elif PY_VERSION_HEX >= 0x030a0000
        node.frame_is_executing = (gen_frame != NULL) && _PyFrame_IsExecuting(&f);
#else
        node.gi_running = gen.gi_running;
#endif
        chain[depth] = node;

        if (yf == NULL || yf == cur) {
            break;
        }
        cur = yf;
    }

    if (chain.empty()) {
        return ErrorKind::GenInfoError;
    }

    // is_running propagates from the leaf (deepest awaited coroutine) all the way
    // up to the root: each coroutine that is awaiting another one is "running" iff
    // the coroutine it awaits is running.
    bool chain_is_running = false;
    {
        const auto& leaf = chain.back();
#if PY_VERSION_HEX >= 0x030b0000
        chain_is_running = (leaf.gi_frame_state == FRAME_EXECUTING);
#elif PY_VERSION_HEX >= 0x030a0000
        chain_is_running = leaf.frame_is_executing;
#else
        chain_is_running = (leaf.gi_running != 0);
#endif
    }

    // Build the GenInfo linked list from the leaf back to the root.
    GenInfo::Ptr result = nullptr;
    for (auto it = chain.rbegin(); it != chain.rend(); ++it) {
        result = std::make_unique<GenInfo>(it->origin, it->frame, std::move(result), chain_is_running);
    }

    return result;
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

    auto maybe_name = echion.string_table().lookup(frame.name);
    if (!maybe_name) {
        return false;
    }
    const auto& frame_name = maybe_name->get();

#if PY_VERSION_HEX >= 0x030b0000
    // Python 3.11+: qualified name includes the enclosing function
    constexpr std::string_view wrapper_name = "run.<locals>.wrapper";
    return frame_name == wrapper_name;
#else
    // Python < 3.11: just check for "wrapper" in uvloop/__init__.py
    constexpr std::string_view uvloop_init_py = "uvloop/__init__.py";
    constexpr std::string_view wrapper = "wrapper";
    auto maybe_filename = echion.string_table().lookup(frame.filename);
    if (!maybe_filename) {
        return false;
    }
    const auto& filename = maybe_filename->get();
    auto is_uvloop = filename.size() >= uvloop_init_py.size() &&
                     filename.rfind(uvloop_init_py) == filename.size() - uvloop_init_py.size();
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
    size_t coro_chain_depth = 0;
    for (auto py_coro = this->coro.get(); py_coro != NULL; py_coro = py_coro->await.get()) {
        coro_chain_depth++;
        if (coro_chain_depth > MAX_RECURSION_DEPTH) {
            break;
        }

        if (py_coro->frame != NULL) {
            coro_frames.push(py_coro->frame);
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
        if (!stack.empty() && is_uvloop_wrapper_frame(echion, using_uvloop, stack.back())) {
            stack.pop_back();
            continue;
        }

        count += 1;
    }

    return count;
}
