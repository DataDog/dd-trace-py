#include <echion/state.h>
#include <echion/tasks.h>
#include <echion/threads.h>

#include <echion/echion_sampler.h>

#include <algorithm>
#include <optional>
#include <string_view>

namespace {
size_t
select_frame_prefix(const FrameStack& frames, size_t start, size_t count, size_t budget, size_t& locations)
{
    size_t selected = 0;
    while (selected < count) {
        const size_t frame_locations = rendered_location_count(frames[start + selected]);
        if (frame_locations > budget - locations) {
            break;
        }
        locations += frame_locations;
        selected++;
    }
    return selected;
}
} // namespace

void
ThreadInfo::unwind(EchionSampler& echion, PyThreadState* tstate)
{
    const size_t max_frames = echion.stack_max_frames();
    asyncio_boundary_index.reset();

    if (asyncio_loop) {
        // Materialize at most max_frames physical frames. Boundary discovery is
        // performed within those candidates. If an on-CPU boundary lies beyond
        // the budget, unwind_tasks conservatively reports task context alone
        // rather than walking or stitching an unbounded physical stack.
        unwind_python_stack(echion, tstate, python_stack, max_frames);
        for (size_t i = 0; i < python_stack.size(); i++) {
            if (is_asyncio_boundary_frame(echion, python_stack[i])) {
                asyncio_boundary_index = i;
                break;
            }
        }

        // unwind_tasks returns a [[nodiscard]] Result<void>.
        // We cast it to void to ignore failures.
        (void)unwind_tasks(echion, tstate);
    } else {
        unwind_python_stack(echion, tstate, python_stack, max_frames);
        // We make the assumption that gevent and asyncio are not mixed
        // together to keep the logic here simple. We can always revisit this
        // should there be a substantial demand for it.
        unwind_greenlets(echion, tstate, native_id);
    }
}

bool
ThreadInfo::is_asyncio_boundary_frame(EchionSampler& echion, const Frame& frame)
{
    // Check if the Python stack contains the asyncio boundary frame.
    // For regular asyncio, this is "Handle._run" from asyncio/events.py.
    // For uvloop, this is "Runner.run" from asyncio/runners.py (uvloop uses asyncio.Runner internally).
    // To avoid having to do string comparisons every time we unwind Tasks, we keep track
    // of the cache key of the boundary frame.

    // Note: We use separate cache keys for asyncio and uvloop because switching between them
    // (though unlikely at runtime) would cause incorrect boundary detection otherwise.
    auto& asyncio_frame_cache_key = echion.asyncio_frame_cache_key();
    auto& uvloop_frame_cache_key = echion.uvloop_frame_cache_key();

    auto& frame_cache_key = using_uvloop ? uvloop_frame_cache_key : asyncio_frame_cache_key;

    if (frame_cache_key) {
        return frame.cache_key == *frame_cache_key;
    }

    auto maybe_frame_name = echion.string_table().lookup(frame.name);
    if (!maybe_frame_name) {
        return false;
    }
    const auto& frame_name = maybe_frame_name->get();

    bool is_boundary_frame = false;
    if (using_uvloop) {
#if PY_VERSION_HEX >= 0x030b0000
        constexpr std::string_view runner_run = "Runner.run";
        is_boundary_frame = frame_name == runner_run;
#else
        constexpr std::string_view uvloop_init_py = "uvloop/__init__.py";
        constexpr std::string_view run = "run";
        auto maybe_filename = echion.string_table().lookup(frame.filename);
        if (!maybe_filename) {
            return false;
        }
        const auto& filename = maybe_filename->get();
        auto is_uvloop = filename.size() >= uvloop_init_py.size() &&
                         filename.rfind(uvloop_init_py) == filename.size() - uvloop_init_py.size();
        is_boundary_frame = is_uvloop && frame_name == run;
#endif
    } else {
#if PY_VERSION_HEX >= 0x030b0000
        constexpr std::string_view handle_run = "Handle._run";
        is_boundary_frame = frame_name == handle_run;
#else
        constexpr std::string_view asyncio_events_py = "asyncio/events.py";
        constexpr std::string_view run = "_run";
        auto maybe_filename = echion.string_table().lookup(frame.filename);
        if (!maybe_filename) {
            return false;
        }
        const auto& filename = maybe_filename->get();
        auto is_asyncio = filename.size() >= asyncio_events_py.size() &&
                          filename.rfind(asyncio_events_py) == filename.size() - asyncio_events_py.size();
        is_boundary_frame =
          is_asyncio && frame_name.size() >= run.size() && frame_name.rfind(run) == frame_name.size() - run.size();
#endif
    }

    if (is_boundary_frame) {
        frame_cache_key = frame.cache_key;
    }
    return is_boundary_frame;
}

// ----------------------------------------------------------------------------
Result<void>
ThreadInfo::unwind_tasks(EchionSampler& echion, PyThreadState* tstate)
{
    std::vector<TaskInfo::Ref> leaf_tasks;
    std::unordered_set<PyObject*> parent_tasks;
    std::unordered_map<PyObject*, TaskInfo::Ref> waitee_map; // Indexed by task origin
    std::unordered_map<PyObject*, TaskInfo::Ref> origin_map; // Indexed by task origin

    auto maybe_all_tasks = get_all_tasks(echion, tstate);
    if (!maybe_all_tasks) {
        return ErrorKind::TaskInfoError;
    }

    auto all_tasks = std::move(*maybe_all_tasks);
    echion.add_asyncio_task_count(all_tasks.size());
    {
        auto& previous_task_objects = echion.previous_task_objects();
        std::lock_guard<std::mutex> lock(echion.task_link_map_lock());

        auto& task_link_map = echion.task_link_map();
        auto& weak_task_link_map = echion.weak_task_link_map();

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

    // Pre-compute at most max_frames coroutine frames per task. Task linkage and
    // coroutine depth remain available as metadata, but frames that cannot be
    // reported are not materialized.
    struct CachedTaskStack
    {
        FrameStack frames;
        size_t depth = 0;
    };
    const size_t max_frames = echion.stack_max_frames();
    std::unordered_map<PyObject*, CachedTaskStack> task_coro_stacks;
    for (auto& task : all_tasks) {
        CachedTaskStack cached;
        auto result = task->unwind(echion, cached.frames, using_uvloop, max_frames);
        cached.depth = result.depth;
        task_coro_stacks.emplace(task->origin, std::move(cached));
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
        // Must match _task.task_object_address() so lock and stack samples correlate.
        auto task_id = reinterpret_cast<uintptr_t>(leaf_task.get().origin);
        auto stack_info = std::make_unique<StackInfo>(leaf_task.get().name, leaf_task.get().is_on_cpu, task_id);
        FrameStack task_frames;
        size_t on_cpu_task_depth = 0;
        bool has_on_cpu_task = false;

        // Safety: prevent infinite loops from cycles in task chain maps.
        size_t task_chain_depth = 0;
        for (auto current_task = leaf_task; task_frames.size() < max_frames;) {
            if (++task_chain_depth > MAX_RECURSION_DEPTH) {
                break;
            }
            auto& task = current_task.get();

            if (auto it = task_coro_stacks.find(task.origin); it != task_coro_stacks.end()) {
                const auto& cached = it->second;
                const size_t frames_to_push = std::min(cached.frames.size(), max_frames - task_frames.size());
                task_frames.insert(task_frames.end(), cached.frames.begin(), cached.frames.begin() + frames_to_push);
                if (task.is_on_cpu) {
                    has_on_cpu_task = true;
                    on_cpu_task_depth = cached.depth;
                }
            }

            if (task_frames.size() >= max_frames) {
                break;
            }

            PyObject* task_origin = task.origin;
            if (auto maybe_waitee = waitee_map.find(task_origin); maybe_waitee != waitee_map.end()) {
                current_task = maybe_waitee->second;
                continue;
            }

            bool found_parent = false;
            {
                std::lock_guard<std::mutex> lock(echion.task_link_map_lock());
                auto& task_link_map = echion.task_link_map();
                auto& weak_task_link_map = echion.weak_task_link_map();

                if (auto maybe_parent = task_link_map.find(task_origin); maybe_parent != task_link_map.end()) {
                    if (auto maybe_origin = origin_map.find(maybe_parent->second); maybe_origin != origin_map.end()) {
                        current_task = maybe_origin->second;
                        found_parent = true;
                    }
                }

                if (!found_parent) {
                    auto weak_parent = weak_task_link_map.find(task_origin);
                    if (weak_parent != weak_task_link_map.end()) {
                        if (auto maybe_origin = origin_map.find(weak_parent->second);
                            maybe_origin != origin_map.end()) {
                            current_task = maybe_origin->second;
                            found_parent = true;
                        }
                    }
                }
            }

            if (!found_parent) {
                break;
            }
        }

        // AIDEV-NOTE: Task candidates are selected before physical candidates,
        // but rendered in logical leaf-to-root order below. Keep budgeting and
        // output ordering separate when changing this stitching policy.
        // Apply the configured limit to rendered locations. A Python frame can
        // inject one native location, so counting Frame objects alone can let a
        // lower-priority physical segment displace task context in the exporter.
        const bool task_collection_full = task_frames.size() >= max_frames;
        size_t task_locations = 0;
        const size_t task_to_keep = select_frame_prefix(task_frames, 0, task_frames.size(), max_frames, task_locations);
        const bool task_truncated = task_collection_full || task_to_keep < task_frames.size();
        task_frames.erase(task_frames.begin() + task_to_keep, task_frames.end());

        // Task frames receive the reporting budget first. If capacity remains,
        // fill it with physical context. For an on-CPU task, split that context
        // around the coroutine frames replaced by task_frames:
        //
        //   synchronous leaf frames, logical task frames, event-loop roots
        //
        // Synchronous leaf frames have priority over event-loop roots.
        if (task_truncated || (has_on_cpu_task && !asyncio_boundary_index)) {
            stack_info->stack = std::move(task_frames);
        } else {
            auto& stack = stack_info->stack;
            size_t locations = task_locations;

            if (has_on_cpu_task && asyncio_boundary_index) {
                const size_t leaf_depth =
                  *asyncio_boundary_index > on_cpu_task_depth ? *asyncio_boundary_index - on_cpu_task_depth : 0;
                const size_t leaf_available = std::min(leaf_depth, python_stack.size());
                const size_t runtime_start = std::min(*asyncio_boundary_index, python_stack.size());
                const size_t runtime_available = python_stack.size() - runtime_start;

                size_t leaf_to_keep = select_frame_prefix(python_stack, 0, leaf_available, max_frames, locations);
                size_t runtime_to_keep =
                  select_frame_prefix(python_stack, runtime_start, runtime_available, max_frames, locations);
                bool leaf_truncated = leaf_depth > leaf_to_keep;
                bool runtime_truncated = runtime_available > runtime_to_keep;

                // An omission marker consumes one location. Remove the
                // lowest-priority selected physical frame if the real locations
                // have already filled the budget.
                if ((leaf_truncated || runtime_truncated) && locations >= max_frames) {
                    if (runtime_to_keep > 0) {
                        runtime_to_keep--;
                        locations -= rendered_location_count(python_stack[runtime_start + runtime_to_keep]);
                    } else if (leaf_to_keep > 0) {
                        leaf_to_keep--;
                        locations -= rendered_location_count(python_stack[leaf_to_keep]);
                    }
                    leaf_truncated = leaf_depth > leaf_to_keep;
                    runtime_truncated = runtime_available > runtime_to_keep;
                }

                stack.insert(stack.end(), python_stack.begin(), python_stack.begin() + leaf_to_keep);
                if (leaf_truncated) {
                    stack_info->omission_index = stack.size();
                    stack_info->omitted_frames = leaf_depth - leaf_to_keep;
                }
                stack.insert(stack.end(), task_frames.begin(), task_frames.end());
                stack.insert(stack.end(),
                             python_stack.begin() + runtime_start,
                             python_stack.begin() + runtime_start + runtime_to_keep);
                if (!leaf_truncated && runtime_truncated) {
                    stack_info->omission_index = stack.size();
                    stack_info->omitted_frames = runtime_available - runtime_to_keep;
                }
            } else {
                // An off-CPU task is followed by the event-loop thread stack.
                size_t sync_to_keep = select_frame_prefix(python_stack, 0, python_stack.size(), max_frames, locations);
                bool sync_truncated = python_stack.size() > sync_to_keep;
                if (sync_truncated && locations >= max_frames && sync_to_keep > 0) {
                    sync_to_keep--;
                    locations -= rendered_location_count(python_stack[sync_to_keep]);
                }

                stack = std::move(task_frames);
                stack.insert(stack.end(), python_stack.begin(), python_stack.begin() + sync_to_keep);
                if (sync_truncated) {
                    stack_info->omission_index = stack.size();
                    stack_info->omitted_frames = python_stack.size() - sync_to_keep;
                }
            }
        }

        current_tasks.push_back(std::move(stack_info));
    }

    return Result<void>::ok();
}

// ----------------------------------------------------------------------------
#if PY_VERSION_HEX >= 0x030e0000
Result<void>
ThreadInfo::get_tasks_from_thread_linked_list(EchionSampler& echion, std::vector<TaskInfo::Ptr>& tasks)
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

    return get_tasks_from_linked_list(echion, head_addr, tasks);
}

Result<void>
ThreadInfo::get_tasks_from_interpreter_linked_list(EchionSampler& echion,
                                                   PyThreadState* tstate,
                                                   std::vector<TaskInfo::Ptr>& tasks)
{
    if (tstate == nullptr || tstate->interp == nullptr || this->asyncio_loop == 0) {
        return ErrorKind::TaskInfoError;
    }

    constexpr size_t asyncio_tasks_head_offset = offsetof(PyInterpreterState, asyncio_tasks_head);
    uintptr_t head_addr = reinterpret_cast<uintptr_t>(tstate->interp) + asyncio_tasks_head_offset;

    return get_tasks_from_linked_list(echion, head_addr, tasks);
}

Result<void>
ThreadInfo::get_tasks_from_linked_list(EchionSampler& echion, uintptr_t head_addr, std::vector<TaskInfo::Ptr>& tasks)
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
        auto maybe_task_info = TaskInfo::create(echion, reinterpret_cast<TaskObj*>(task_addr_uint));
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
ThreadInfo::get_all_tasks(EchionSampler& echion, PyThreadState* tstate)
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
        (void)get_tasks_from_thread_linked_list(echion, tasks);

        // Second, get tasks from interpreter's linked-list (lingering tasks)
        (void)get_tasks_from_interpreter_linked_list(echion, tstate, tasks);
    }

    // Handle third-party tasks from Python _scheduled_tasks WeakSet
    // In Python 3.14+, _scheduled_tasks is a Python-level weakref.WeakSet() that only contains
    // tasks that don't inherit from asyncio.Task. Native asyncio.Task instances are stored
    // in linked-lists (handled above) and are NOT added to _scheduled_tasks.
    // This is typically empty in practice, but we handle it for completeness.
    auto asyncio_scheduled_tasks = echion.asyncio_scheduled_tasks();
    if (asyncio_scheduled_tasks != nullptr) {
        if (auto maybe_scheduled_tasks_set = MirrorSet::create(asyncio_scheduled_tasks)) {
            auto scheduled_tasks_set = std::move(*maybe_scheduled_tasks_set);
            if (auto maybe_scheduled_tasks = scheduled_tasks_set.as_unordered_set()) {
                auto scheduled_tasks = std::move(*maybe_scheduled_tasks);
                for (auto task_addr : scheduled_tasks) {
                    // In WeakSet.data (set), elements are the Task objects themselves
                    auto maybe_task_info = TaskInfo::create(echion, reinterpret_cast<TaskObj*>(task_addr));
                    if (maybe_task_info &&
                        (*maybe_task_info)->loop == reinterpret_cast<PyObject*>(this->asyncio_loop)) {
                        tasks.push_back(std::move(*maybe_task_info));
                    }
                }
            }
        }
    }

    auto asyncio_eager_tasks = echion.asyncio_eager_tasks();
    if (asyncio_eager_tasks != nullptr) {
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
            auto maybe_task_info = TaskInfo::create(echion, reinterpret_cast<TaskObj*>(task_addr));
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
ThreadInfo::get_all_tasks(EchionSampler& echion, PyThreadState*)
{
    std::vector<TaskInfo::Ptr> tasks;
    if (this->asyncio_loop == 0)
        return tasks;

    auto asyncio_scheduled_tasks = echion.asyncio_scheduled_tasks();
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

        auto maybe_task_info = TaskInfo::create(echion, reinterpret_cast<TaskObj*>(task_wr.wr_object));
        if (maybe_task_info) {
            if (reinterpret_cast<uintptr_t>((*maybe_task_info)->loop) == this->asyncio_loop) {
                tasks.push_back(std::move(*maybe_task_info));
            }
        }
    }

    auto asyncio_eager_tasks = echion.asyncio_eager_tasks();
    if (asyncio_eager_tasks != nullptr) {
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
            auto maybe_task_info = TaskInfo::create(echion, reinterpret_cast<TaskObj*>(task_addr));
            if (maybe_task_info) {
                if (reinterpret_cast<uintptr_t>((*maybe_task_info)->loop) == this->asyncio_loop) {
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
ThreadInfo::unwind_greenlets(EchionSampler& echion, PyThreadState* tstate, unsigned long cur_native_id)
{
    std::vector<GreenletSnapshot> snapshots;

    // Phase 1: Snapshot greenlet data under the lock.
    // This minimises the time we hold greenlet_info_map_lock, which is also
    // acquired by update_greenlet_frame() on every greenlet switch.  Holding
    // the lock during the expensive unwind (Phase 2) would block ALL greenlet
    // switches and lead to resource exhaustion (e.g. DB connection pools).
    {
        const std::lock_guard<std::mutex> guard(echion.greenlet_info_map_lock());

        auto& greenlet_info_map = echion.greenlet_info_map();
        auto& greenlet_parent_map = echion.greenlet_parent_map();
        auto& greenlet_thread_map = echion.greenlet_thread_map();

        if (greenlet_thread_map.find(cur_native_id) == greenlet_thread_map.end())
            return;

        std::unordered_set<GreenletInfo::ID> parent_greenlets;

        // Collect all parent greenlets
        std::transform(greenlet_parent_map.cbegin(),
                       greenlet_parent_map.cend(),
                       std::inserter(parent_greenlets, parent_greenlets.begin()),
                       [](const std::pair<GreenletInfo::ID, GreenletInfo::ID>& kv) { return kv.second; });

        // Snapshot the leaf greenlets and precompute their parent chains
        for (auto& [gid, greenlet] : greenlet_info_map) {
            if (parent_greenlets.contains(gid))
                continue;

            auto frame = greenlet->frame;
            if (frame == FRAME_NOT_SET) {
                // The greenlet has not been started yet or has finished
                continue;
            }

            GreenletSnapshot snap{ gid, greenlet->name, frame, {} };

            // Precompute parent chain while we still hold the lock
            auto current_id = gid;
            std::unordered_set<GreenletInfo::ID> visited;
            // The limit here is arbitrary, but it should be more than enough for
            // most use cases.
            const size_t MAX_GREENLET_DEPTH = 512;
            // Safety: prevent infinite loops from cycles or corrupted parent maps
            for (size_t iteration_count = 0; iteration_count < MAX_GREENLET_DEPTH; ++iteration_count) {
                // Check for cycles
                if (visited.contains(current_id))
                    break;
                visited.insert(current_id);

                auto pit = greenlet_parent_map.find(current_id);
                if (pit == greenlet_parent_map.end())
                    break;

                auto parent_id = pit->second;
                auto git = greenlet_info_map.find(parent_id);
                if (git == greenlet_info_map.end())
                    break;

                auto parent_frame = git->second->frame;
                if (parent_frame == FRAME_NOT_SET || parent_frame == Py_None)
                    break;

                snap.parent_chain.emplace_back(git->second->name, parent_frame);

                // Move up the greenlet chain
                current_id = parent_id;
            }

            snapshots.push_back(std::move(snap));
        }
    } // Lock released here

    // Phase 2: Unwind outside the lock.
    // The expensive process_vm_readv / copy_type calls happen here, without
    // blocking greenlet switches.  Snapshotted frame pointers may have become
    // stale, but unwind_frame() handles invalid pointers gracefully via
    // copy_type() which returns non-zero on failure.
    for (auto& snap : snapshots) {
        bool on_cpu = snap.frame == Py_None;
        auto stack_info = std::make_unique<StackInfo>(snap.name, on_cpu, snap.greenlet_id);
        auto& stack = stack_info->stack;

        GreenletInfo temp(snap.greenlet_id, snap.frame, snap.name);
        temp.unwind(echion, snap.frame, tstate, stack);

        for (auto& [parent_name, parent_frame] : snap.parent_chain) {
            GreenletInfo parent_temp(0, parent_frame, parent_name);
            parent_temp.unwind(echion, parent_frame, tstate, stack);
        }

        current_greenlets.push_back(std::move(stack_info));
    }

    // Make sure the on-CPU greenlet is first. render_task_begin reuses the
    // sample created by render_thread_begin for the first task it renders;
    // that sample already received push_cputime via render_cpu_time. Tasks
    // rendered after the first start a new sample and, if on_cpu is true,
    // push thread_state.cpu_time_ns again, double-counting CPU time.
    //
    // unwind_tasks performs the analogous swap on leaf_tasks above. Note that
    // the "on-CPU" signal differs: asyncio's is_on_cpu is derived from frame
    // matching during unwind, while a greenlet's on_cpu is set from
    // snap.frame == Py_None (see the loop above), which is the sentinel
    // greenlet uses for its currently-running greenlet. If that sentinel
    // changes, this swap silently no-ops and the over-count returns.
    //
    // If no greenlet is on CPU (e.g. all workers are sleeping while the Hub
    // is running, which is filtered out as a parent), no entry triggers
    // render_task_begin's push_cputime branch, so order does not matter and
    // this loop falls through harmlessly. Empty current_greenlets is also
    // safe (loop body never executes).
    for (size_t i = 1; i < current_greenlets.size(); i++) {
        if (current_greenlets[i]->on_cpu) {
            std::swap(current_greenlets[i], current_greenlets[0]);
            break;
        }
    }
}

// ----------------------------------------------------------------------------
void
ThreadInfo::render_unwound_stacks(EchionSampler& echion)
{
    auto& renderer = echion.renderer();

    // Render in this order of priority
    // 1. asyncio Tasks stacks (if any)
    // 2. Greenlets stacks (if any)
    // 3. The normal thread stack (if no asyncio tasks or greenlets)
    if (!current_tasks.empty()) {
        for (auto& task_stack_info : current_tasks) {
            task_stack_info->task_name.visit_string([&](std::string_view task_name) {
                renderer.render_task_begin(task_name, task_stack_info->on_cpu, task_stack_info->task_id);
            });

            task_stack_info->stack.render(echion, task_stack_info->omission_index, task_stack_info->omitted_frames);

            renderer.render_stack_end();
        }

        current_tasks.clear();
    } else if (!current_greenlets.empty()) {
        for (auto& greenlet_stack : current_greenlets) {
            greenlet_stack->task_name.visit_string([&](std::string_view task_name) {
                renderer.render_task_begin(task_name, greenlet_stack->on_cpu, greenlet_stack->task_id);
            });

            auto& stack = greenlet_stack->stack;
            stack.render(echion);

            renderer.render_stack_end();
        }

        current_greenlets.clear();
    } else {
        python_stack.render(echion);
        renderer.render_stack_end();
    }
}

// ----------------------------------------------------------------------------
Result<void>
ThreadInfo::sample(EchionSampler& echion, PyThreadState* tstate, microsecond_t delta)
{
    auto& renderer = echion.renderer();
    renderer.render_thread_begin(tstate, name, delta, thread_id, native_id);

    microsecond_t previous_cpu_time = cpu_time;
    auto update_cpu_time_success = update_cpu_time();
    if (!update_cpu_time_success) {
        return ErrorKind::CpuTimeError;
    }

    renderer.render_cpu_time(cpu_time - previous_cpu_time);

    this->unwind(echion, tstate);
    this->render_unwound_stacks(echion);

    return Result<void>::ok();
}

Result<void>
ThreadInfo::update_cpu_time()
{
#if defined PL_LINUX
    struct timespec ts;
    if (clock_gettime(cpu_clock_id, &ts)) {
        // If the clock is invalid, we skip updating the CPU time.
        // This can happen if we try to compute CPU time for a thread that has exited.
        if (errno == EINVAL) {
            return Result<void>::ok();
        }

        return ErrorKind::CpuTimeError;
    }

    this->cpu_time = TS_TO_MICROSECOND(ts);
#elif defined PL_DARWIN
    thread_basic_info_data_t info;
    mach_msg_type_number_t count = THREAD_BASIC_INFO_COUNT;
    kern_return_t kr = thread_info(
      static_cast<thread_act_t>(this->mach_port), THREAD_BASIC_INFO, reinterpret_cast<thread_info_t>(&info), &count);

    if (kr != KERN_SUCCESS) {
        // If the thread is invalid, we skip updating the CPU time.
        // This can happen if we try to compute CPU time for a thread that has exited.
        if (kr == KERN_INVALID_ARGUMENT) {
            return Result<void>::ok();
        }

        return ErrorKind::CpuTimeError;
    }

    if (info.flags & TH_FLAGS_IDLE) {
        return Result<void>::ok();
    }

    this->cpu_time = TV_TO_MICROSECOND(info.user_time) + TV_TO_MICROSECOND(info.system_time);
#endif

    return Result<void>::ok();
}

void
for_each_thread(EchionSampler& echion, InterpreterInfo& interp, const PyThreadStateCallback& callback)
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
