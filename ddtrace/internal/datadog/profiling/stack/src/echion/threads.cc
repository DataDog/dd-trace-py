#include "sampler.hpp"
#include <echion/echion_sampler.h>
#include <echion/threads.h>

#include <algorithm>
#include <optional>

void
ThreadInfo::unwind(PyThreadState* tstate)
{
    unwind_python_stack(tstate, python_stack);

    if (asyncio_loop) {
        // unwind_tasks returns a [[nodiscard]] Result<void>.
        // We cast it to void to ignore failures.
        (void)unwind_tasks(tstate);
    } else {
        // We make the assumption that gevent and asyncio are not mixed
        // together to keep the logic here simple. We can always revisit this
        // should there be a substantial demand for it.
        unwind_greenlets(tstate, native_id);
    }

    if (Datadog::Sampler::get().exception_profiling_enabled()) {
        std::cerr << "Exception profiling enabled; force aborting" << std::endl;
        abort();
    }
}

// ----------------------------------------------------------------------------






// ----------------------------------------------------------------------------
Result<void>
ThreadInfo::unwind_tasks(PyThreadState* tstate)
{
    // The size of the "pure Python" stack (before asyncio Frames).
    // Defaults to the full Python stack size (and updated if we find the "_run" Frame)
    size_t upper_python_stack_size = python_stack.size();

    // Check if the Python stack contains "_run".
    // To avoid having to do string comparisons every time we unwind Tasks, we keep track
    // of the cache key of the "_run" Frame.
    static std::optional<Frame::Key> frame_cache_key;
    if (!frame_cache_key) {
        for (size_t i = 0; i < python_stack.size(); i++) {
            const auto& frame = python_stack[i].get();
            const auto& frame_name = string_table.lookup(frame.name)->get();

#if PY_VERSION_HEX >= 0x030b0000
            // After Python 3.11, function names in Frames are qualified with e.g. the class name, so we
            // can use the qualified name to identify the "_run" Frame.
            constexpr std::string_view _run = "Handle._run";
            auto is_run_frame = frame_name == _run;
#else
            // Before Python 3.11, function names in Frames are not qualified, so we
            // can use the filename to identify the "_run" Frame.
            constexpr std::string_view asyncio_runners_py = "asyncio/events.py";
            constexpr std::string_view _run = "_run";
            auto filename = string_table.lookup(frame.filename)->get();
            auto is_asyncio = filename.rfind(asyncio_runners_py) == filename.size() - asyncio_runners_py.size();
            auto is_run_frame = is_asyncio && (frame_name.rfind(_run) == frame_name.size() - _run.size());
#endif
            if (is_run_frame) {
                // Although Frames are stored in an LRUCache, the cache key is ALWAYS the same
                // even if the Frame gets evicted from the cache.
                // This means we can keep the cache key and re-use it to determine
                // whether we see the "_run" Frame in the Python stack.
                frame_cache_key = frame.cache_key;
                upper_python_stack_size = python_stack.size() - i;
                break;
            }
        }
    } else {
        for (size_t i = 0; i < python_stack.size(); i++) {
            const auto& frame = python_stack[i].get();
            if (frame.cache_key == *frame_cache_key) {
                upper_python_stack_size = python_stack.size() - i;
                break;
            }
        }
    }

    std::vector<TaskInfo::Ref> leaf_tasks;
    std::unordered_set<PyObject*> parent_tasks;
    std::unordered_map<PyObject*, TaskInfo::Ref> waitee_map; // Indexed by task origin
    std::unordered_map<PyObject*, TaskInfo::Ref> origin_map; // Indexed by task origin
    static std::unordered_set<PyObject*> previous_task_objects;

    auto maybe_all_tasks = get_all_tasks(tstate);
    if (!maybe_all_tasks) {
        return ErrorKind::TaskInfoError;
    }

    auto all_tasks = std::move(*maybe_all_tasks);
    {
        std::lock_guard<std::mutex> lock(task_link_map_lock);

        // Clean up the task_link_map. Remove entries associated to tasks that
        // no longer exist.
        std::unordered_set<PyObject*> all_task_origins;
        std::transform(all_tasks.cbegin(),
                       all_tasks.cend(),
                       std::inserter(all_task_origins, all_task_origins.begin()),
                       [](const TaskInfo::Ptr& task) { return task->origin; });

        std::vector<PyObject*> to_remove;
        for (auto kv : task_link_map) {
            if (all_task_origins.find(kv.first) == all_task_origins.end())
                to_remove.push_back(kv.first);
        }
        for (auto key : to_remove) {
            // Only remove the link if the Child Task previously existed; otherwise it's a Task that
            // has just been created and that wasn't in all_tasks when we took the snapshot.
            if (auto it = previous_task_objects.find(key); it != previous_task_objects.end()) {
                task_link_map.erase(key);
            }
        }

        // Determine the parent tasks from the gather links.
        std::transform(task_link_map.cbegin(),
                       task_link_map.cend(),
                       std::inserter(parent_tasks, parent_tasks.begin()),
                       [](const std::pair<PyObject*, PyObject*>& kv) { return kv.second; });

        // Clean up the weak_task_link_map.
        // Remove entries associated to tasks that no longer exist.
        all_task_origins.clear();
        std::transform(all_tasks.cbegin(),
                       all_tasks.cend(),
                       std::inserter(all_task_origins, all_task_origins.begin()),
                       [](const TaskInfo::Ptr& task) { return task->origin; });

        to_remove.clear();
        for (auto kv : weak_task_link_map) {
            if (all_task_origins.find(kv.first) == all_task_origins.end())
                to_remove.push_back(kv.first);
        }

        for (auto key : to_remove) {
            weak_task_link_map.erase(key);
        }

        // Determine the parent tasks from the gather (strong) links.
        for (auto& link : task_link_map) {
            auto parent = link.second;

            // Check if the parent is actually the child of another Task
            auto is_child = weak_task_link_map.find(parent) != weak_task_link_map.end();

            // Only insert if we do not know of a Task that created the current Task
            if (!is_child) {
                parent_tasks.insert(parent);
            }
        }

        // Copy all Task object pointers into previous_task_objects
        previous_task_objects.clear();
        for (const auto& task : all_tasks) {
            previous_task_objects.insert(task->origin);
        }
    }

    for (auto& task : all_tasks) {
        origin_map.emplace(task->origin, std::ref(*task));

        if (task->waiter != nullptr)
            waitee_map.emplace(task->waiter->origin, std::ref(*task));
        else if (parent_tasks.find(task->origin) == parent_tasks.end()) {
            leaf_tasks.push_back(std::ref(*task));
        }
    }

    // Make sure the on CPU task is first
    for (size_t i = 0; i < leaf_tasks.size(); i++) {
        if (leaf_tasks[i].get().is_on_cpu) {
            if (i > 0) {
                std::swap(leaf_tasks[i], leaf_tasks[0]);
            }
            break;
        }
    }

    for (auto& leaf_task : leaf_tasks) {
        auto stack_info = std::make_unique<StackInfo>(leaf_task.get().name, leaf_task.get().is_on_cpu);
        auto& stack = stack_info->stack;

        for (auto current_task = leaf_task;;) {
            auto& task = current_task.get();

            auto task_stack_size = task.unwind(stack);
            if (task.is_on_cpu) {
                // Get the "bottom" part of the Python synchronous Stack, that is to say the
                // synchronous functions and coroutines called by the Task's outermost coroutine
                // The number of Frames to push is the total number of Frames in the Python stack, from which we
                // subtract the number of Frames in the "upper Python stack" (asyncio machinery + sync entrypoint)
                // This gives us [outermost coroutine, ... , innermost coroutine, outermost sync function, ... ,
                // innermost sync function]
                // TODO: This may be incorrect if the Task that we know is on CPU does not match the Task that
                //       actually was on CPU when the Python Thread Stack was captured. One way to work around this
                //       may be to look at every Task Stack and match it against the Thread Stack. This would be
                //       somewhat costly though, and so far I have not seen a single instance of this race condition.
                size_t frames_to_push = (python_stack.size() > upper_python_stack_size + task_stack_size)
                                          ? python_stack.size() - upper_python_stack_size - task_stack_size
                                          : 0;
                for (size_t i = 0; i < frames_to_push; i++) {
                    const auto& python_frame = python_stack[frames_to_push - i - 1];
                    stack.push_front(python_frame);
                }
            }

            // Add the task name frame
            stack.push_back(Frame::get(task.name));

            // Get the next task in the chain
            PyObject* task_origin = task.origin;
            if (auto maybe_waitee = waitee_map.find(task_origin); maybe_waitee != waitee_map.end()) {
                current_task = maybe_waitee->second;
                continue;
            }

            {
                // Check for, e.g., gather links
                std::lock_guard<std::mutex> lock(task_link_map_lock);

                if (auto maybe_parent = task_link_map.find(task_origin); maybe_parent != task_link_map.end()) {
                    if (auto maybe_origin = origin_map.find(maybe_parent->second); maybe_origin != origin_map.end()) {
                        current_task = maybe_origin->second;
                        continue;
                    }
                }
            }

            // Check for weak links
            if (weak_task_link_map.find(task_origin) != weak_task_link_map.end() &&
                origin_map.find(weak_task_link_map[task_origin]) != origin_map.end()) {
                current_task = origin_map.find(weak_task_link_map[task_origin])->second;
                continue;
            }

            break;
        }

        // Finish off with the remaining thread stack
        // If we have seen an on-CPU Task, then upper_python_stack_size will be set and will include the sync entry
        // point and the asyncio machinery Frames. Otherwise, we are in `select` (idle) and we should push all the
        // Frames.

        // There could be a race condition where relevant partial Python Thread Stack ends up being different from the
        // one we saw in TaskInfo::unwind. This is extremely unlikely, I believe, but failing to account for it would
        // cause an underflow, so let's be conservative.
        size_t start_index = 0;
        if (python_stack.size() >= upper_python_stack_size) {
            start_index = python_stack.size() - upper_python_stack_size;
        }
        for (size_t i = start_index; i < python_stack.size(); i++) {
            const auto& python_frame = python_stack[i];
            stack.push_back(python_frame);
        }

        current_tasks.push_back(std::move(stack_info));
    }

    return Result<void>::ok();
}

// ----------------------------------------------------------------------------
#if PY_VERSION_HEX >= 0x030e0000
Result<void>
ThreadInfo::get_tasks_from_thread_linked_list(std::vector<TaskInfo::Ptr>& tasks)
{
    if (this->tstate_addr == 0 || this->asyncio_loop == 0) {
        return ErrorKind::TaskInfoError;
    }

    // Calculate thread state's asyncio_tasks_head remote address
    // Note: Since 3.13+, every PyThreadState is actually allocated as a _PyThreadStateImpl.
    // We use PyThreadState* everywhere and cast to _PyThreadStateImpl* only when we need
    // to access asyncio_tasks_head (which is only available in Python 3.14+).
    // Since tstate_addr is a remote address, we calculate the offset and add it to the address.
    // get_tasks_from_linked_list will handle copying the head node from remote memory internally.
    constexpr size_t asyncio_tasks_head_offset = offsetof(_PyThreadStateImpl, asyncio_tasks_head);
    uintptr_t head_addr = this->tstate_addr + asyncio_tasks_head_offset;

    return get_tasks_from_linked_list(head_addr, tasks);
}

Result<void>
ThreadInfo::get_tasks_from_interpreter_linked_list(PyThreadState* tstate, std::vector<TaskInfo::Ptr>& tasks)
{
    if (tstate == nullptr || tstate->interp == nullptr || this->asyncio_loop == 0) {
        return ErrorKind::TaskInfoError;
    }

    constexpr size_t asyncio_tasks_head_offset = offsetof(PyInterpreterState, asyncio_tasks_head);
    uintptr_t head_addr = reinterpret_cast<uintptr_t>(tstate->interp) + asyncio_tasks_head_offset;

    return get_tasks_from_linked_list(head_addr, tasks);
}

Result<void>
ThreadInfo::get_tasks_from_linked_list(uintptr_t head_addr, std::vector<TaskInfo::Ptr>& tasks)
{
    if (head_addr == 0 || this->asyncio_loop == 0) {
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
            if (task_info->loop == reinterpret_cast<PyObject*>(this->asyncio_loop)) {
                tasks.push_back(std::move(task_info));
            }
        }

        // Read next node from current_node.next into current_node
        if (copy_type(reinterpret_cast<void*>(next_node_addr), current_node)) {
            return ErrorKind::TaskInfoError; // Failed to read next node
        }
    }

    return Result<void>::ok();
}

Result<std::vector<TaskInfo::Ptr>>
ThreadInfo::get_all_tasks(PyThreadState* tstate)
{
    std::vector<TaskInfo::Ptr> tasks;
    if (this->asyncio_loop == 0)
        return tasks;

    // Python 3.14+: Native tasks are in linked-list per thread AND per interpreter
    // CPython iterates over both:
    // 1. Per-thread list: tstate->asyncio_tasks_head (active tasks)
    // 2. Per-interpreter list: interp->asyncio_tasks_head (lingering tasks)
    // First, get tasks from this thread's linked-list (if tstate_addr is set)
    // Note: We continue processing even if one source fails to maximize partial results
    if (tstate != nullptr && this->tstate_addr != 0) {
        (void)get_tasks_from_thread_linked_list(tasks);

        // Second, get tasks from interpreter's linked-list (lingering tasks)
        (void)get_tasks_from_interpreter_linked_list(tstate, tasks);
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
                    if (maybe_task_info &&
                        (*maybe_task_info)->loop == reinterpret_cast<PyObject*>(this->asyncio_loop)) {
                        tasks.push_back(std::move(*maybe_task_info));
                    }
                }
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
                if ((*maybe_task_info)->loop == reinterpret_cast<PyObject*>(this->asyncio_loop)) {
                    tasks.push_back(std::move(*maybe_task_info));
                }
            }
        }
    }

    return tasks;
}
#else
// Pre-Python 3.14: get_all_tasks uses WeakSet approach
Result<std::vector<TaskInfo::Ptr>>
ThreadInfo::get_all_tasks(PyThreadState*)
{
    std::vector<TaskInfo::Ptr> tasks;
    if (this->asyncio_loop == 0)
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
            if ((*maybe_task_info)->loop == reinterpret_cast<PyObject*>(this->asyncio_loop)) {
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
                if ((*maybe_task_info)->loop == reinterpret_cast<PyObject*>(this->asyncio_loop)) {
                    tasks.push_back(std::move(*maybe_task_info));
                }
            }
        }
    }

    return tasks;
}
#endif // PY_VERSION_HEX >= 0x030e0000

// ----------------------------------------------------------------------------
void
ThreadInfo::unwind_greenlets(PyThreadState* tstate, unsigned long cur_native_id)
{
    const std::lock_guard<std::mutex> guard(greenlet_info_map_lock);

    if (greenlet_thread_map.find(cur_native_id) == greenlet_thread_map.end())
        return;

    std::unordered_set<GreenletInfo::ID> parent_greenlets;

    // Collect all parent greenlets
    std::transform(greenlet_parent_map.cbegin(),
                   greenlet_parent_map.cend(),
                   std::inserter(parent_greenlets, parent_greenlets.begin()),
                   [](const std::pair<GreenletInfo::ID, GreenletInfo::ID>& kv) { return kv.second; });

    // Unwind the leaf greenlets
    for (auto& greenlet_info : greenlet_info_map) {
        auto greenlet_id = greenlet_info.first;
        auto& greenlet = greenlet_info.second;

        if (parent_greenlets.contains(greenlet_id))
            continue;

        auto frame = greenlet->frame;
        if (frame == FRAME_NOT_SET) {
            // The greenlet has not been started yet or has finished
            continue;
        }

        bool on_cpu = frame == Py_None;

        auto stack_info = std::make_unique<StackInfo>(greenlet->name, on_cpu);
        auto& stack = stack_info->stack;

        greenlet->unwind(frame, tstate, stack);

        std::unordered_set<GreenletInfo::ID> visited;

        // Unwind the parent greenlets
        // The limit here is arbitrary, but it should be more than enough for
        // most use cases.
        const size_t MAX_GREENLET_DEPTH = 512;
        // Safety: prevent infinite loops from cycles or corrupted parent maps
        for (size_t iteration_count = 0; iteration_count < MAX_GREENLET_DEPTH; ++iteration_count) {
            // Check for cycles
            if (visited.contains(greenlet_id)) {
                break;
            }
            visited.insert(greenlet_id);

            auto parent_greenlet_info = greenlet_parent_map.find(greenlet_id);
            if (parent_greenlet_info == greenlet_parent_map.end())
                break;

            auto parent_greenlet_id = parent_greenlet_info->second;

            auto parent_greenlet = greenlet_info_map.find(parent_greenlet_id);
            if (parent_greenlet == greenlet_info_map.end())
                break;

            auto parent_frame = parent_greenlet->second->frame;
            if (parent_frame == FRAME_NOT_SET || parent_frame == Py_None)
                break;

            parent_greenlet->second->unwind(parent_frame, tstate, stack);

            // Move up the greenlet chain
            greenlet_id = parent_greenlet_id;
        }

        current_greenlets.push_back(std::move(stack_info));
    }
}

// ----------------------------------------------------------------------------
Result<void>
ThreadInfo::sample(int64_t iid, PyThreadState* tstate, microsecond_t delta)
{
    Renderer::get().render_thread_begin(tstate, name, delta, thread_id, native_id);

    microsecond_t previous_cpu_time = cpu_time;
    auto update_cpu_time_success = update_cpu_time();
    if (!update_cpu_time_success) {
        return ErrorKind::CpuTimeError;
    }

    Renderer::get().render_cpu_time(is_running() ? cpu_time - previous_cpu_time : 0);

    this->unwind(tstate);

    // Render in this order of priority
    // 1. asyncio Tasks stacks (if any)
    // 2. Greenlets stacks (if any)
    // 3. The normal thread stack (if no asyncio tasks or greenlets)
    if (!current_tasks.empty()) {
        for (auto& task_stack_info : current_tasks) {
            auto maybe_task_name = string_table.lookup(task_stack_info->task_name);
            if (!maybe_task_name) {
                return ErrorKind::ThreadInfoError;
            }

            const auto& task_name = maybe_task_name->get();
            Renderer::get().render_task_begin(task_name, task_stack_info->on_cpu);
            Renderer::get().render_stack_begin(pid, iid, name);

            task_stack_info->stack.render();

            Renderer::get().render_stack_end(MetricType::Time, delta);
        }

        current_tasks.clear();
    } else if (!current_greenlets.empty()) {
        for (auto& greenlet_stack : current_greenlets) {
            auto maybe_task_name = string_table.lookup(greenlet_stack->task_name);
            if (!maybe_task_name) {
                return ErrorKind::ThreadInfoError;
            }

            const auto& task_name = maybe_task_name->get();
            Renderer::get().render_task_begin(task_name, greenlet_stack->on_cpu);
            Renderer::get().render_stack_begin(pid, iid, name);

            auto& stack = greenlet_stack->stack;
            stack.render();

            Renderer::get().render_stack_end(MetricType::Time, delta);
        }

        current_greenlets.clear();
    } else {
        Renderer::get().render_stack_begin(pid, iid, name);
        python_stack.render();
        Renderer::get().render_stack_end(MetricType::Time, delta);
    }

    return Result<void>::ok();
}

Result<void>
ThreadInfo::update_cpu_time()
{
#if defined PL_LINUX
    struct timespec ts1;
    if (clock_gettime(cpu_clock_id, &ts1)) {
        // If the clock is invalid, we skip updating the CPU time.
        // This can happen if we try to compute CPU time for a thread that has exited.
        if (errno == EINVAL) {
            this->running_ = false;
            return Result<void>::ok();
        }

        return ErrorKind::CpuTimeError;
    }

    this->cpu_time = TS_TO_MICROSECOND(ts1);

    // Determine if running by checking if CPU time advances between two back-to-back
    // measurements. This is done here to avoid a separate is_running() call with
    // its own syscalls (reduces 3 syscalls per thread to 2).
    struct timespec ts2;
    if (clock_gettime(cpu_clock_id, &ts2) != 0) {
        this->running_ = false;
    } else {
        this->running_ = (ts1.tv_sec != ts2.tv_sec || ts1.tv_nsec != ts2.tv_nsec);
    }
#elif defined PL_DARWIN
    thread_basic_info_data_t info;
    mach_msg_type_number_t count = THREAD_BASIC_INFO_COUNT;
    kern_return_t kr = thread_info(
      static_cast<thread_act_t>(this->mach_port), THREAD_BASIC_INFO, reinterpret_cast<thread_info_t>(&info), &count);

    if (kr != KERN_SUCCESS) {
        // If the thread is invalid, we skip updating the CPU time.
        // This can happen if we try to compute CPU time for a thread that has exited.
        if (kr == KERN_INVALID_ARGUMENT) {
            this->running_ = false;
            return Result<void>::ok();
        }

        return ErrorKind::CpuTimeError;
    }

    if (info.flags & TH_FLAGS_IDLE) {
        this->running_ = false;
        return Result<void>::ok();
    }

    this->cpu_time = TV_TO_MICROSECOND(info.user_time) + TV_TO_MICROSECOND(info.system_time);
    // On macOS, thread_info already gives us run_state, so no need to check if the clock is advancing
    this->running_ = (info.run_state == TH_STATE_RUNNING);
#endif

    return Result<void>::ok();
}

bool
ThreadInfo::is_running()
{
    // Running state is computed in update_cpu_time by taking two back-to-back measurements of the CPU time.
    return this->running_;
}

void
for_each_thread(EchionSampler& echion, InterpreterInfo& interp, PyThreadStateCallback callback)
{
    std::unordered_set<PyThreadState*> threads;
    std::unordered_set<PyThreadState*> seen_threads;

    // Start from the thread list head
    threads.insert(static_cast<PyThreadState*>(interp.tstate_head));

    while (!threads.empty()) {
        // Pop the next thread
        PyThreadState* tstate_addr = *threads.begin();
        threads.erase(threads.begin());

        // Mark the thread as seen
        seen_threads.insert(tstate_addr);

        // Since threads can be created and destroyed at any time, we make
        // a copy of the structure before trying to read its fields.
        PyThreadState tstate;
        if (copy_type(tstate_addr, tstate))
            // We failed to copy the thread so we skip it.
            continue;

        // Enqueue the unseen threads that we can reach from this thread.
        if (tstate.next != NULL && seen_threads.find(tstate.next) == seen_threads.end())
            threads.insert(tstate.next);
        if (tstate.prev != NULL && seen_threads.find(tstate.prev) == seen_threads.end())
            threads.insert(tstate.prev);

        {
            const std::lock_guard<std::mutex> guard(echion.thread_info_map_lock());

            auto it = echion.thread_info_map().find(tstate.thread_id);
            if (it == echion.thread_info_map().end()) {
                // We failed to find ThreadInfo for thread_id, maybe there's a
                // race condition between this call and `register_thread()`.
                continue;
            }

            // Update the tstate_addr for thread info, so we can access
            // asyncio_tasks_head field from `_PyThreadStateImpl` struct
            // later when we unwind tasks.
            auto thread_info = it->second.get();
            thread_info->tstate_addr = reinterpret_cast<uintptr_t>(tstate_addr);

            // Call back with the copied thread state
            callback(&tstate, *thread_info);
        }
    }
}
