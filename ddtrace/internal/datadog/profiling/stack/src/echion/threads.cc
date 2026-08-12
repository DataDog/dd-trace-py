#include <echion/state.h>
#include <echion/tasks.h>
#include <echion/threads.h>

#include "cpu_timer.hpp"

#include <echion/echion_sampler.h>

#include "dd_wrapper/include/defer.hpp"

#include <algorithm>
#include <optional>
#include <string_view>

void
ThreadInfo::reset_cycle_state() noexcept
{
    current_tasks.clear();
    current_greenlets.clear();
}

void
ThreadInfo::unwind(EchionSampler& echion, PyThreadState* tstate)
{
    // This entry reset is a precondition for a new snapshot: never append to logical state from an earlier cycle.
    reset_cycle_state();

    unwind_python_stack(echion, tstate, python_stack);

    if (asyncio_loop) {
        // unwind_tasks returns a [[nodiscard]] Result<void>.
        // We cast it to void to ignore failures.
        (void)unwind_tasks(echion, tstate);
    } else {
        // We make the assumption that gevent and asyncio are not mixed
        // together to keep the logic here simple. We can always revisit this
        // should there be a substantial demand for it.
        unwind_greenlets(echion, tstate, native_id);
    }
}

// ----------------------------------------------------------------------------
Result<void>
ThreadInfo::unwind_tasks(EchionSampler& echion, PyThreadState* tstate)
{
    // The size of the "pure Python" stack (before asyncio Frames).
    // Defaults to the full Python stack size (and updated if we find the boundary frame)
    size_t upper_python_stack_size = python_stack.size();

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

    if (!frame_cache_key) {
        for (size_t i = 0; i < python_stack.size(); i++) {
            const auto& frame = python_stack[i];
            auto maybe_frame_name = echion.string_table().lookup(frame.name);
            if (!maybe_frame_name) {
                continue;
            }
            const auto& frame_name = maybe_frame_name->get();

            bool is_boundary_frame = false;

            if (using_uvloop) {
                // For uvloop, the boundary frame depends on the Python version:
                // - Python 3.11+: Runner.run from asyncio/runners.py (uvloop uses asyncio.Runner)
                // - Python < 3.11: run from uvloop/__init__.py (uvloop has its own implementation)
#if PY_VERSION_HEX >= 0x030b0000
                constexpr std::string_view runner_run = "Runner.run";
                is_boundary_frame = frame_name == runner_run;
#else
                constexpr std::string_view uvloop_init_py = "uvloop/__init__.py";
                constexpr std::string_view run = "run";
                auto maybe_filename = echion.string_table().lookup(frame.filename);
                if (!maybe_filename) {
                    continue;
                }
                const auto& filename = maybe_filename->get();
                auto is_uvloop = filename.rfind(uvloop_init_py) == filename.size() - uvloop_init_py.size();
                is_boundary_frame = is_uvloop && (frame_name == run);
#endif
            } else {
                // For regular asyncio, the boundary frame is Handle._run from asyncio/events.py
#if PY_VERSION_HEX >= 0x030b0000
                // After Python 3.11, function names in Frames are qualified with e.g. the class name, so we
                // can use the qualified name to identify the "_run" Frame.
                constexpr std::string_view _run = "Handle._run";
                is_boundary_frame = frame_name == _run;
#else
                // Before Python 3.11, function names in Frames are not qualified, so we
                // can use the filename to identify the "_run" Frame.
                constexpr std::string_view asyncio_events_py = "asyncio/events.py";
                constexpr std::string_view _run = "_run";
                auto maybe_filename = echion.string_table().lookup(frame.filename);
                if (!maybe_filename) {
                    continue;
                }
                const auto& filename = maybe_filename->get();
                auto is_asyncio = filename.size() >= asyncio_events_py.size() &&
                                  filename.rfind(asyncio_events_py) == filename.size() - asyncio_events_py.size();
                is_boundary_frame = is_asyncio && (frame_name.size() >= _run.size() &&
                                                   frame_name.rfind(_run) == frame_name.size() - _run.size());
#endif
            }

            if (is_boundary_frame) {
                // Although Frames are stored in an LRUCache, the cache key is ALWAYS the same
                // even if the Frame gets evicted from the cache.
                // This means we can keep the cache key and reuse it to determine
                // whether we see the boundary Frame in the Python stack.
                frame_cache_key = frame.cache_key;
                upper_python_stack_size = python_stack.size() - i;
                break;
            }
        }
    } else {
        for (size_t i = 0; i < python_stack.size(); i++) {
            const auto& frame = python_stack[i];
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

    // Pre-compute per-task coroutine stacks so that each task's coroutine chain is walked exactly once.
    // Without this, a parent task's coroutine chain would be walked once for each child task that
    // references it in its task chain (e.g. 10 children from asyncio.gather = 10 redundant unwinds
    // of the parent's coroutine chain).
    std::unordered_map<PyObject*, FrameStack> task_coro_stacks;
    for (auto& task : all_tasks) {
        FrameStack task_stack;
        task->unwind(echion, task_stack, using_uvloop);
        task_coro_stacks.emplace(task->origin, std::move(task_stack));
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
        auto& stack = stack_info->stack;

        // Safety: prevent infinite loops from cycles in task chain maps
        size_t task_chain_depth = 0;
        for (auto current_task = leaf_task;;) {
            if (++task_chain_depth > MAX_RECURSION_DEPTH) {
                break;
            }
            auto& task = current_task.get();

            // Look up the pre-computed coroutine stack for this task.
            // FrameStack order is leaf-to-root. For on-CPU tasks, synchronous frames from
            // python_stack must be appended before coroutine frames.
            // Decide how many coroutine frames to keep before appending the on-CPU sync frames below.
            // This preserves the previous max_frames truncation behavior while avoiding front insertion.
            const FrameStack* task_stack = nullptr;
            size_t task_stack_size = 0;
            size_t task_frames_to_push = 0;
            if (auto it = task_coro_stacks.find(task.origin); it != task_coro_stacks.end()) {
                task_stack = &it->second;
                task_stack_size = task_stack->size();
                if (stack.size() < max_frames) {
                    task_frames_to_push = std::min(task_stack_size, max_frames - stack.size());
                }
            }
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
                // These frames should render before the coroutine frames. Append them first in leaf-to-root order.
                for (size_t i = 0; i < frames_to_push; i++) {
                    const auto& python_frame = python_stack[i];

                    // Skip the uvloop wrapper frame if present in the Python stack
                    if (is_uvloop_wrapper_frame(echion, using_uvloop, python_frame)) {
                        continue;
                    }
                    stack.push_back(python_frame);
                }
            }
            if (task_stack != nullptr) {
                for (size_t i = 0; i < task_frames_to_push; i++) {
                    stack.push_back((*task_stack)[i]);
                }
            }

            // Task labels are rendered separately from frames; do not add a synthetic
            // frame for the task name here.

            // Get the next task in the chain
            PyObject* task_origin = task.origin;
            if (auto maybe_waitee = waitee_map.find(task_origin); maybe_waitee != waitee_map.end()) {
                current_task = maybe_waitee->second;
                continue;
            }

            {
                // Check for, e.g., gather links
                std::lock_guard<std::mutex> lock(echion.task_link_map_lock());
                auto& task_link_map = echion.task_link_map();
                auto& weak_task_link_map = echion.weak_task_link_map();

                if (auto maybe_parent = task_link_map.find(task_origin); maybe_parent != task_link_map.end()) {
                    if (auto maybe_origin = origin_map.find(maybe_parent->second); maybe_origin != origin_map.end()) {
                        current_task = maybe_origin->second;
                        continue;
                    }
                }

                // Check for weak links
                if (weak_task_link_map.find(task_origin) != weak_task_link_map.end() &&
                    origin_map.find(weak_task_link_map[task_origin]) != origin_map.end()) {
                    current_task = origin_map.find(weak_task_link_map[task_origin])->second;
                    continue;
                }
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
ThreadInfo::get_task_addresses_from_thread_linked_list(std::vector<TaskObj*>& tasks)
{
    if (this->tstate_addr == 0 || this->asyncio_loop == 0) {
        return ErrorKind::TaskInfoError;
    }

    constexpr size_t asyncio_tasks_head_offset = offsetof(_PyThreadStateImpl, asyncio_tasks_head);
    return get_task_addresses_from_linked_list(this->tstate_addr + asyncio_tasks_head_offset, tasks);
}

Result<void>
ThreadInfo::get_task_addresses_from_interpreter_linked_list(PyThreadState* tstate, std::vector<TaskObj*>& tasks)
{
    if (tstate == nullptr || tstate->interp == nullptr || this->asyncio_loop == 0) {
        return ErrorKind::TaskInfoError;
    }

    constexpr size_t asyncio_tasks_head_offset = offsetof(PyInterpreterState, asyncio_tasks_head);
    const uintptr_t head_addr = reinterpret_cast<uintptr_t>(tstate->interp) + asyncio_tasks_head_offset;
    return get_task_addresses_from_linked_list(head_addr, tasks);
}

Result<void>
ThreadInfo::get_task_addresses_from_linked_list(uintptr_t head_addr, std::vector<TaskObj*>& tasks)
{
    if (head_addr == 0 || this->asyncio_loop == 0) {
        return ErrorKind::TaskInfoError;
    }

    struct llist_node current_node;
    if (copy_type(reinterpret_cast<void*>(head_addr), current_node)) {
        return ErrorKind::TaskInfoError;
    }

    const uintptr_t head_addr_uint = head_addr;
    if (reinterpret_cast<uintptr_t>(current_node.next) == head_addr_uint &&
        reinterpret_cast<uintptr_t>(current_node.prev) == head_addr_uint) {
        return Result<void>::ok();
    }

    constexpr size_t max_iterations = 1 << 16;
    size_t iteration_count = 0;
    while (reinterpret_cast<uintptr_t>(current_node.next) != head_addr_uint) {
        if (++iteration_count > max_iterations || current_node.next == nullptr) {
            return ErrorKind::TaskInfoError;
        }

        const uintptr_t next_node_addr = reinterpret_cast<uintptr_t>(current_node.next);
        const uintptr_t task_addr = next_node_addr - offsetof(TaskObj, task_node);
        tasks.push_back(reinterpret_cast<TaskObj*>(task_addr));

        if (copy_type(reinterpret_cast<void*>(next_node_addr), current_node)) {
            return ErrorKind::TaskInfoError;
        }
    }

    return Result<void>::ok();
}

Result<std::vector<TaskObj*>>
ThreadInfo::get_all_task_addresses(EchionSampler& echion, PyThreadState* tstate)
{
    std::vector<TaskObj*> tasks;
    if (this->asyncio_loop == 0) {
        return tasks;
    }

    // Native tasks can appear in both lists. Consumers already tolerate duplicate task objects.
    if (this->tstate_addr != 0) {
        (void)get_task_addresses_from_thread_linked_list(tasks);
    }
    if (tstate != nullptr) {
        (void)get_task_addresses_from_interpreter_linked_list(tstate, tasks);
    }

    // Python 3.14 stores only third-party Task implementations in _scheduled_tasks.
    if (auto scheduled = echion.asyncio_scheduled_tasks(); scheduled != nullptr) {
        if (auto maybe_set = MirrorSet::create(scheduled)) {
            if (auto maybe_tasks = maybe_set->as_unordered_set()) {
                for (auto task : *maybe_tasks) {
                    tasks.push_back(reinterpret_cast<TaskObj*>(task));
                }
            }
        }
    }

    if (auto eager = echion.asyncio_eager_tasks(); eager != nullptr) {
        auto maybe_set = MirrorSet::create(eager);
        if (!maybe_set) {
            return ErrorKind::TaskInfoError;
        }
        auto maybe_tasks = maybe_set->as_unordered_set();
        if (!maybe_tasks) {
            return ErrorKind::TaskInfoError;
        }
        for (auto task : *maybe_tasks) {
            tasks.push_back(reinterpret_cast<TaskObj*>(task));
        }
    }

    return tasks;
}
#else
Result<std::vector<TaskObj*>>
ThreadInfo::get_all_task_addresses(EchionSampler& echion, PyThreadState*)
{
    std::vector<TaskObj*> tasks;
    if (this->asyncio_loop == 0) {
        return tasks;
    }

    auto maybe_set = MirrorSet::create(echion.asyncio_scheduled_tasks());
    if (!maybe_set) {
        return ErrorKind::TaskInfoError;
    }
    auto maybe_scheduled = maybe_set->as_unordered_set();
    if (!maybe_scheduled) {
        return ErrorKind::TaskInfoError;
    }
    for (auto weakref_addr : *maybe_scheduled) {
        PyWeakReference weakref;
        if (!copy_type(weakref_addr, weakref)) {
            tasks.push_back(reinterpret_cast<TaskObj*>(weakref.wr_object));
        }
    }

    if (auto eager = echion.asyncio_eager_tasks(); eager != nullptr) {
        auto maybe_eager_set = MirrorSet::create(eager);
        if (!maybe_eager_set) {
            return ErrorKind::TaskInfoError;
        }
        auto maybe_eager = maybe_eager_set->as_unordered_set();
        if (!maybe_eager) {
            return ErrorKind::TaskInfoError;
        }
        for (auto task : *maybe_eager) {
            tasks.push_back(reinterpret_cast<TaskObj*>(task));
        }
    }

    return tasks;
}
#endif // PY_VERSION_HEX >= 0x030e0000

Result<std::vector<TaskInfo::Ptr>>
ThreadInfo::get_all_tasks(EchionSampler& echion, PyThreadState* tstate)
{
    auto maybe_addresses = get_all_task_addresses(echion, tstate);
    if (!maybe_addresses) {
        return maybe_addresses.error();
    }

    std::vector<TaskInfo::Ptr> tasks;
    for (TaskObj* task_addr : *maybe_addresses) {
        auto maybe_task = TaskInfo::create(echion, task_addr);
        if (maybe_task && reinterpret_cast<uintptr_t>((*maybe_task)->loop) == this->asyncio_loop) {
            tasks.push_back(std::move(*maybe_task));
        }
    }
    return tasks;
}

// ----------------------------------------------------------------------------
namespace {

std::optional<GreenletSnapshot>
snapshot_greenlet(EchionSampler& echion, GreenletInfo::ID greenlet_id)
{
    const std::lock_guard<std::mutex> guard(echion.greenlet_info_map_lock());
    auto& greenlets = echion.greenlet_info_map();
    auto selected = greenlets.find(greenlet_id);
    if (selected == greenlets.end() || selected->second->frame == FRAME_NOT_SET) {
        return std::nullopt;
    }

    GreenletSnapshot snapshot{ greenlet_id, selected->second->name, selected->second->frame, {} };
    auto& parents = echion.greenlet_parent_map();
    std::unordered_set<GreenletInfo::ID> visited;
    GreenletInfo::ID current = greenlet_id;
    constexpr size_t max_greenlet_depth = 512;
    for (size_t depth = 0; depth < max_greenlet_depth && visited.insert(current).second; depth++) {
        auto parent = parents.find(current);
        if (parent == parents.end()) {
            break;
        }
        auto parent_info = greenlets.find(parent->second);
        if (parent_info == greenlets.end() || parent_info->second->frame == FRAME_NOT_SET ||
            parent_info->second->frame == Py_None) {
            break;
        }
        snapshot.parent_chain.emplace_back(parent_info->second->name, parent_info->second->frame);
        current = parent->second;
    }
    return snapshot;
}

} // namespace

void
ThreadInfo::unwind_greenlets(EchionSampler& echion, PyThreadState* tstate, unsigned long cur_native_id)
{
    std::vector<GreenletSnapshot> snapshots;

    // Phase 1: Snapshot greenlet data under the lock.
    // This minimises the time we hold greenlet_info_map_lock, which is also
    // acquired by record_greenlet_switch() on every greenlet switch. Holding
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

namespace {

using Datadog::CpuTimer::CoroutineFingerprint;
using Datadog::CpuTimer::RawSample;

bool
fingerprint_matches_frame(const CoroutineFingerprint& fingerprint, const Frame& frame)
{
    return fingerprint.code_object == frame.code_object && fingerprint.lasti == frame.lasti &&
           fingerprint.first_lineno == frame.first_lineno;
}

const GenInfo*
active_coroutine(const TaskInfo& task)
{
    const GenInfo* active = task.coro.get();
    while (active != nullptr && active->await != nullptr) {
        active = active->await.get();
    }
    return active;
}

struct TaskIdentity
{
    TaskObj* origin;
    PyObject* coroutine;
    PyObject* waiter;
};

bool
raw_contains_coroutine(const RawSample& raw, PyObject* coroutine)
{
    for (uint8_t i = 0; i < raw.coroutine_fingerprint_count; i++) {
        if (raw.coroutine_fingerprints[i].coroutine == reinterpret_cast<uintptr_t>(coroutine)) {
            return true;
        }
    }
    return false;
}

bool
coroutine_chain_matches(PyObject* coroutine, const RawSample& raw)
{
    for (size_t depth = 0; coroutine != nullptr && depth < MAX_RECURSION_DEPTH; depth++) {
        if (raw_contains_coroutine(raw, coroutine)) {
            return true;
        }

        PyGenObject gen;
        if (copy_type(coroutine, gen)) {
            return false;
        }
        PyTypeObject* type = gen.ob_base.ob_type;
        if (type == &_PyAsyncGenASend_Type) {
            PyAsyncGenASend asend;
            if (copy_type(coroutine, asend)) {
                return false;
            }
            coroutine = reinterpret_cast<PyObject*>(asend.ags_gen);
            continue;
        }
        if (type != &PyCoro_Type && type != &PyAsyncGen_Type) {
            return false;
        }

#if PY_VERSION_HEX >= 0x030b0000
        PyObject* frame =
          gen.gi_frame_state == FRAME_CLEARED
            ? nullptr
            : reinterpret_cast<PyObject*>(reinterpret_cast<char*>(coroutine) + offsetof(PyGenObject, gi_iframe));
#else
        PyObject* frame = reinterpret_cast<PyObject*>(gen.gi_frame);
#endif
        PyObject* awaited = frame != nullptr ? PyGen_yf(&gen, frame) : nullptr;
        if (awaited == nullptr || awaited == coroutine) {
            return false;
        }
        coroutine = awaited;
    }
    return false;
}

template<typename Predicate>
TaskObj*
find_unique_task(const std::vector<TaskIdentity>& tasks, Predicate&& matches)
{
    TaskObj* selected = nullptr;
    for (const auto& task : tasks) {
        if (!matches(task)) {
            continue;
        }
        if (selected != nullptr && selected != task.origin) {
            return nullptr;
        }
        selected = task.origin;
    }
    return selected;
}

TaskObj*
find_captured_task(const std::vector<TaskIdentity>& tasks, const RawSample& raw)
{
    if (raw.asyncio_task != 0) {
        return find_unique_task(tasks, [&](const TaskIdentity& task) {
            return reinterpret_cast<uintptr_t>(task.origin) == raw.asyncio_task;
        });
    }

    // The task's root coroutine is normally present in the captured physical stack.
    // Check it first so unrelated tasks do not require await-chain traversal.
    TaskObj* root_match = nullptr;
    for (const auto& task : tasks) {
        if (!raw_contains_coroutine(raw, task.coroutine)) {
            continue;
        }
        if (root_match != nullptr && root_match != task.origin) {
            return nullptr;
        }
        root_match = task.origin;
    }
    if (root_match != nullptr) {
        return root_match;
    }

    return find_unique_task(tasks,
                            [&](const TaskIdentity& task) { return coroutine_chain_matches(task.coroutine, raw); });
}

void
unwind_selected_task(EchionSampler& echion,
                     TaskInfo& selected,
                     const std::vector<TaskIdentity>& tasks,
                     bool using_uvloop,
                     FrameStack& stack)
{
    std::unordered_map<PyObject*, TaskObj*> tasks_by_origin;
    std::unordered_map<PyObject*, PyObject*> waiter_parents;
    for (const auto& task : tasks) {
        tasks_by_origin.emplace(reinterpret_cast<PyObject*>(task.origin), task.origin);
        if (task.waiter != nullptr) {
            waiter_parents.emplace(task.waiter, reinterpret_cast<PyObject*>(task.origin));
        }
    }

    std::unordered_set<PyObject*> visited;
    TaskInfo* current = &selected;
    TaskInfo::Ptr parent;
    for (size_t depth = 0; current != nullptr && depth < MAX_RECURSION_DEPTH; depth++) {
        if (!visited.insert(current->origin).second) {
            break;
        }
        (void)current->unwind(echion, stack, using_uvloop);

        PyObject* parent_origin = nullptr;
        if (auto waiter_parent = waiter_parents.find(current->origin); waiter_parent != waiter_parents.end()) {
            parent_origin = waiter_parent->second;
        } else {
            std::lock_guard<std::mutex> lock(echion.task_link_map_lock());
            auto& task_link_map = echion.task_link_map();
            auto& weak_task_link_map = echion.weak_task_link_map();
            if (auto task_parent = task_link_map.find(current->origin); task_parent != task_link_map.end()) {
                parent_origin = task_parent->second;
            } else if (auto weak_parent = weak_task_link_map.find(current->origin);
                       weak_parent != weak_task_link_map.end()) {
                parent_origin = weak_parent->second;
            }
        }

        auto parent_address = tasks_by_origin.find(parent_origin);
        if (parent_address == tasks_by_origin.end()) {
            break;
        }
        auto maybe_parent = TaskInfo::create(echion, parent_address->second);
        if (!maybe_parent) {
            break;
        }
        parent = std::move(*maybe_parent);
        current = parent.get();
    }
}

const CoroutineFingerprint*
matching_active_fingerprint(EchionSampler& echion, const TaskInfo& task, const RawSample& raw)
{
    if (!task.is_on_cpu) {
        return nullptr;
    }

    const GenInfo* active = active_coroutine(task);
    if (active == nullptr || active->frame == nullptr) {
        return nullptr;
    }

    const uintptr_t active_origin = reinterpret_cast<uintptr_t>(active->origin);
    const CoroutineFingerprint* fingerprint = nullptr;
    for (uint8_t i = 0; i < raw.coroutine_fingerprint_count; i++) {
        if (raw.coroutine_fingerprints[i].coroutine == active_origin) {
            fingerprint = &raw.coroutine_fingerprints[i];
            break;
        }
    }
    if (fingerprint == nullptr) {
        return nullptr;
    }

    FrameStack active_stack;
    if (unwind_frame(echion, active->frame, active_stack, echion.seen_frames_scratch(), 1) != 1) {
        return nullptr;
    }
    return fingerprint_matches_frame(*fingerprint, active_stack[0]) ? fingerprint : nullptr;
}

bool
greenlet_snapshot_matches(EchionSampler& echion,
                          const GreenletSnapshot& snapshot,
                          PyThreadState* tstate,
                          unsigned long native_id,
                          const FrameStack& captured_stack)
{
    if (snapshot.frame == Py_None) {
        return Datadog::CpuTimer::Engine::get().current_greenlet(native_id) == snapshot.greenlet_id;
    }

    FrameStack snapshot_stack;
    GreenletInfo selected(snapshot.greenlet_id, snapshot.frame, snapshot.name);
    selected.unwind(echion, snapshot.frame, tstate, snapshot_stack);
    return std::any_of(snapshot_stack.begin(), snapshot_stack.end(), [&](const Frame& snapshot_frame) {
        return std::any_of(captured_stack.begin(), captured_stack.end(), [&](const Frame& captured_frame) {
            return snapshot_frame.code_object == captured_frame.code_object &&
                   snapshot_frame.first_lineno == captured_frame.first_lineno;
        });
    });
}

void
append_greenlet_parents(EchionSampler& echion,
                        const GreenletSnapshot& snapshot,
                        PyThreadState* tstate,
                        FrameStack& captured_stack)
{
    for (const auto& [parent_name, parent_frame] : snapshot.parent_chain) {
        FrameStack parent_stack;
        GreenletInfo parent(0, parent_frame, parent_name);
        parent.unwind(echion, parent_frame, tstate, parent_stack);
        for (const Frame& frame : parent_stack) {
            if (captured_stack.size() >= max_frames) {
                return;
            }
            const bool already_captured =
              std::any_of(captured_stack.begin(), captured_stack.end(), [&](const Frame& captured) {
                  return captured.cache_key == frame.cache_key;
              });
            if (!already_captured) {
                captured_stack.push_back(frame);
            }
        }
    }
}

FrameStack
stitch_captured_stack(FrameStack captured_stack,
                      const FrameStack& logical_stack,
                      const CoroutineFingerprint& fingerprint)
{
    auto captured_boundary = std::find_if(captured_stack.begin(), captured_stack.end(), [&](const Frame& frame) {
        return fingerprint_matches_frame(fingerprint, frame);
    });
    auto logical_boundary = std::find_if(logical_stack.begin(), logical_stack.end(), [&](const Frame& frame) {
        return fingerprint_matches_frame(fingerprint, frame);
    });
    if (captured_boundary == captured_stack.end() || logical_boundary == logical_stack.end() ||
        captured_stack.size() >= max_frames) {
        return captured_stack;
    }

    const size_t available = max_frames - captured_stack.size();
    std::vector<Frame> logical_ancestors;
    logical_ancestors.reserve(std::min(available, static_cast<size_t>(logical_stack.end() - logical_boundary - 1)));
    for (auto it = logical_boundary + 1; it != logical_stack.end() && logical_ancestors.size() < available; ++it) {
        const bool already_captured = std::any_of(captured_stack.begin(),
                                                  captured_stack.end(),
                                                  [&](const Frame& frame) { return frame.cache_key == it->cache_key; });
        if (!already_captured) {
            logical_ancestors.push_back(*it);
        }
    }

    captured_stack.insert(captured_boundary + 1, logical_ancestors.begin(), logical_ancestors.end());
    return captured_stack;
}

} // namespace

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

            task_stack_info->stack.render(echion);

            renderer.render_stack_end();
        }
    } else if (!current_greenlets.empty()) {
        for (auto& greenlet_stack : current_greenlets) {
            greenlet_stack->task_name.visit_string([&](std::string_view task_name) {
                renderer.render_task_begin(task_name, greenlet_stack->on_cpu, greenlet_stack->task_id);
            });

            auto& stack = greenlet_stack->stack;
            stack.render(echion);

            renderer.render_stack_end();
        }
    } else {
        python_stack.render(echion);
        renderer.render_stack_end();
    }
}

// ----------------------------------------------------------------------------
Result<void>
ThreadInfo::sample(EchionSampler& echion, PyThreadState* tstate, microsecond_t delta, bool include_cpu_time)
{
    auto& renderer = echion.renderer();

    // This exit reset complements unwind's entry reset. It covers returns before unwind and exceptions after partial
    // task or greenlet state has been populated, so no logical snapshot survives the cycle that created it.
    defer
    {
        reset_cycle_state();
    };

    renderer.render_thread_begin(tstate, name, delta, thread_id, native_id);

    if (include_cpu_time) {
        microsecond_t previous_cpu_time = cpu_time;
        auto update_cpu_time_success = update_cpu_time();
        if (!update_cpu_time_success) {
            return ErrorKind::CpuTimeError;
        }

        renderer.render_cpu_time(cpu_time - previous_cpu_time);
    }

    this->unwind(echion, tstate);
    this->render_unwound_stacks(echion);

    return Result<void>::ok();
}

void
ThreadInfo::sample_cpu_timer(EchionSampler& echion,
                             PyThreadState* tstate,
                             FrameStack&& captured_stack,
                             microsecond_t cpu_time_us,
                             const Datadog::CpuTimer::RawSample& raw)
{
    auto& renderer = echion.renderer();
    renderer.render_cpu_sample_begin(name, cpu_time_us, thread_id, native_id);

    current_tasks.clear();
    current_greenlets.clear();

    // timer_create CPU samples capture physical frames and task
    // identity in the signal handler. A later task snapshot may contribute
    // logical ancestors only after exact object-identity and active-coroutine
    // fingerprint checks. Never select a task from drain-time on_cpu state or
    // code-location overlap alone.
    if (asyncio_loop) {
        // Without signal-time task identity, no drain-time task snapshot can be matched safely.
        if (raw.asyncio_task == 0 && raw.coroutine_fingerprint_count == 0) {
            captured_stack.render(echion);
            renderer.render_stack_end();
            return;
        }

        auto maybe_addresses = get_all_task_addresses(echion, tstate);
        if (maybe_addresses) {
            std::vector<TaskIdentity> tasks;
            tasks.reserve(maybe_addresses->size());
            for (TaskObj* address : *maybe_addresses) {
                TaskObj task;
                if (!copy_type(address, task) && reinterpret_cast<uintptr_t>(task.task_loop) == asyncio_loop) {
                    tasks.push_back({ address, task.task_coro, task.task_fut_waiter });
                }
            }

            if (TaskObj* address = find_captured_task(tasks, raw)) {
                auto maybe_task = TaskInfo::create(echion, address);
                if (maybe_task) {
                    auto task = std::move(*maybe_task);
                    task->name.visit_string([&](std::string_view task_name) {
                        renderer.render_task_begin(task_name, true, reinterpret_cast<uintptr_t>(task->origin));
                    });

                    if (const CoroutineFingerprint* fingerprint = matching_active_fingerprint(echion, *task, raw)) {
                        FrameStack logical_stack;
                        unwind_selected_task(echion, *task, tasks, using_uvloop, logical_stack);
                        captured_stack = stitch_captured_stack(std::move(captured_stack), logical_stack, *fingerprint);
                    }
                }
            }
        }

        captured_stack.render(echion);
        renderer.render_stack_end();
        return;
    }

    if (raw.greenlet_id != 0) {
        auto snapshot = snapshot_greenlet(echion, raw.greenlet_id);
        if (snapshot && greenlet_snapshot_matches(echion, *snapshot, tstate, native_id, captured_stack)) {
            snapshot->name.visit_string(
              [&](std::string_view task_name) { renderer.render_task_begin(task_name, true, snapshot->greenlet_id); });
            append_greenlet_parents(echion, *snapshot, tstate, captured_stack);
        }
    }

    captured_stack.render(echion);
    renderer.render_stack_end();
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

#if PY_VERSION_HEX >= 0x030c0000
        const uint64_t native_thread_id = tstate.native_thread_id;
#else
        const uint64_t native_thread_id = 0;
#endif

        {
            const std::lock_guard<std::mutex> guard(echion.thread_info_map_lock());

            auto it = echion.thread_info_map().find(tstate.thread_id);
            if (it == echion.thread_info_map().end()) {
                // PyThreadState is copied from a concurrently changing interpreter
                // list. Do not create ThreadInfo from its pthread_t: the thread may
                // already have exited, and pthread_getcpuclockid() is unsafe for a
                // stale pthread_t on glibc.
                continue;
            }

            // timer_create CPU timers are per native thread, while the historical
            // ThreadInfo map is metadata for wall sampling. A thread can have
            // ThreadInfo from best-effort registration but no armed CPU timer, for
            // example an already-existing main thread when profiling starts from an
            // auxiliary thread. Reconcile CPU timer arming from the PyThreadState
            // walk so wall-sampler discovery is the safety net. The CPU timer's
            // supported CPython versions expose native_thread_id here.
            if (native_thread_id != 0 &&
                !Datadog::CpuTimer::Engine::get().has_thread(tstate.thread_id, native_thread_id)) {
                Datadog::CpuTimer::Engine::get().register_thread(
                  tstate.thread_id, native_thread_id, "Thread", tstate_addr);
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
