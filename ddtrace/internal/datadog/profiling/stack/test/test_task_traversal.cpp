#include "echion/echion_sampler.h"
#include "echion/threads.h"

#include <gtest/gtest.h>

class ThreadInfoTaskTraversalTest : public ::testing::Test
{
  protected:
#if PY_VERSION_HEX >= 0x030e0000
    // Keep the production traversal private while allowing deterministic linked-list topologies in this test.
    static Result<void> traverse(ThreadInfo& thread,
                                 EchionSampler& echion,
                                 uintptr_t head,
                                 std::vector<TaskInfo::Ptr>& tasks)
    {
        return thread.get_tasks_from_linked_list(echion, head, tasks);
    }

    static Result<std::vector<TaskInfo::Ptr>> get_all_tasks(ThreadInfo& thread,
                                                            EchionSampler& echion,
                                                            PyThreadState* tstate)
    {
        return thread.get_all_tasks(echion, tstate);
    }
#endif
};

#if PY_VERSION_HEX >= 0x030e0000
TEST_F(ThreadInfoTaskTraversalTest, RejectsTaskMovedToAnotherList)
{
    // A real asyncio.Task ensures TaskInfo::create follows the same coroutine and name-reading path as production.
    Py_Initialize();
    PyObject* globals = PyDict_New();
    ASSERT_NE(globals, nullptr);
    ASSERT_EQ(PyDict_SetItemString(globals, "__builtins__", PyEval_GetBuiltins()), 0);

    PyObject* result = PyRun_String(R"(
import asyncio
loop = asyncio.new_event_loop()
async def wait_forever():
    await asyncio.Event().wait()
valid_task = loop.create_task(wait_forever())
task = loop.create_task(wait_forever())
)",
                                    Py_file_input,
                                    globals,
                                    globals);
    ASSERT_NE(result, nullptr);
    Py_DECREF(result);

    auto* loop = PyDict_GetItemString(globals, "loop");
    auto* valid_task = reinterpret_cast<TaskObj*>(PyDict_GetItemString(globals, "valid_task"));
    auto* task = reinterpret_cast<TaskObj*>(PyDict_GetItemString(globals, "task"));
    ASSERT_NE(loop, nullptr);
    ASSERT_NE(valid_task, nullptr);
    ASSERT_NE(task, nullptr);

    EchionSampler echion;
#if defined PL_LINUX
    ThreadInfo thread(1, 1, "test-thread", CLOCK_THREAD_CPUTIME_ID);
#elif defined PL_DARWIN
    ThreadInfo thread(1, 1, "test-thread", mach_thread_self());
#endif
    thread.asyncio_loop = reinterpret_cast<uintptr_t>(loop);

    // Seed the output to verify a failed source preserves tasks previously found by another source.
    std::vector<TaskInfo::Ptr> tasks;
    auto maybe_task = TaskInfo::create(echion, task);
    ASSERT_TRUE(maybe_task);
    tasks.push_back(std::move(*maybe_task));
    TaskInfo* sentinel = tasks.front().get();

    // Model Echion reading A and V from A <-> V <-> T before CPython moves T under head B. Reading T afterward
    // produces this mixed-time view:
    //
    //   copied nodes: A -> V -> T
    //   live task:              B <-> T
    //
    // Traversal appends V before T.prev != V reveals the malformed edge and requires source-local rollback.
    const llist_node original_valid_task_node = valid_task->task_node;
    const llist_node original_task_node = task->task_node;
    llist_node expected_head{};
    llist_node moved_head{};
    expected_head.next = &valid_task->task_node;
    expected_head.prev = &task->task_node;
    valid_task->task_node.prev = &expected_head;
    valid_task->task_node.next = &task->task_node;
    moved_head.next = moved_head.prev = &task->task_node;
    task->task_node.next = task->task_node.prev = &moved_head;

    result = nullptr;
    auto traversal = traverse(thread, echion, reinterpret_cast<uintptr_t>(&expected_head), tasks);

    // Reject the malformed source and roll back only the entries it appended.
    EXPECT_FALSE(traversal);
    EXPECT_EQ(tasks.size(), 1);
    if (!tasks.empty()) {
        EXPECT_EQ(tasks.front().get(), sentinel);
    }

    valid_task->task_node = original_valid_task_node;
    task->task_node = original_task_node;
    tasks.clear();

    // Expose the same Task through a valid thread list and the eager-task set. Cross-source discovery must still
    // return one TaskInfo because downstream accounting and wall-time scaling operate on this result.
    PyObject* eager_tasks = PySet_New(nullptr);
    ASSERT_NE(eager_tasks, nullptr);
    ASSERT_EQ(PySet_Add(eager_tasks, reinterpret_cast<PyObject*>(task)), 0);
    echion.init_asyncio(nullptr, eager_tasks);

    _PyThreadStateImpl remote_tstate{};
    remote_tstate.asyncio_tasks_head.next = remote_tstate.asyncio_tasks_head.prev = &task->task_node;
    task->task_node.next = task->task_node.prev = &remote_tstate.asyncio_tasks_head;
    thread.tstate_addr = reinterpret_cast<uintptr_t>(&remote_tstate);
    PyThreadState local_tstate{};

    auto all_tasks = get_all_tasks(thread, echion, &local_tstate);

    // Restore CPython's real links before cancellation or object destruction can inspect them.
    task->task_node = original_task_node;
    Py_DECREF(eager_tasks);
    ASSERT_TRUE(all_tasks);
    EXPECT_EQ(all_tasks->size(), 1);

    // Process cancellation and close the loop so the real Task does not remain pending at process exit.
    result = PyRun_String(R"(
for pending in (valid_task, task):
    pending.cancel()
for pending in (valid_task, task):
    try:
        loop.run_until_complete(pending)
    except asyncio.CancelledError:
        pass
loop.close()
)",
                          Py_file_input,
                          globals,
                          globals);
    EXPECT_NE(result, nullptr);
    Py_XDECREF(result);
    Py_DECREF(globals);
}
#endif
