#include "echion/echion_sampler.h"
#include "echion/threads.h"

#include <gtest/gtest.h>

class ThreadInfoTaskTraversalTest : public ::testing::Test
{
  protected:
#if PY_VERSION_HEX >= 0x030e0000
    void SetUp() override
    {
        Py_Initialize();
        globals = PyDict_New();
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

        loop = PyDict_GetItemString(globals, "loop");
        valid_task = reinterpret_cast<TaskObj*>(PyDict_GetItemString(globals, "valid_task"));
        task = reinterpret_cast<TaskObj*>(PyDict_GetItemString(globals, "task"));
        ASSERT_NE(loop, nullptr);
        ASSERT_NE(valid_task, nullptr);
        ASSERT_NE(task, nullptr);

#if defined PL_LINUX
        thread = std::make_unique<ThreadInfo>(1, 1, "test-thread", CLOCK_THREAD_CPUTIME_ID);
#elif defined PL_DARWIN
        thread = std::make_unique<ThreadInfo>(1, 1, "test-thread", mach_thread_self());
#endif
        thread->asyncio_loop = reinterpret_cast<uintptr_t>(loop);
        echion.set_asyncio_offsets(AsyncioOffsets{ offsetof(PyInterpreterState, asyncio_tasks_head),
                                                   offsetof(_PyThreadStateImpl, asyncio_tasks_head) });
    }

    void TearDown() override
    {
        if (globals == nullptr || loop == nullptr || valid_task == nullptr || task == nullptr) {
            Py_XDECREF(globals);
            return;
        }

        // Process cancellation and close the loop so no Task remains pending at process exit.
        PyObject* result = PyRun_String(R"(
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

    // Keep the production visitor private while allowing deterministic linked-list topologies in this test.
    static Result<void> visit_thread_tasks(ThreadInfo& thread,
                                           size_t tasks_head_offset,
                                           std::vector<TaskObj*>& task_addresses)
    {
        return thread.for_each_task_address_from_thread_list(tasks_head_offset,
                                                             [&task_addresses](TaskObj* task_address) -> Result<void> {
                                                                 task_addresses.push_back(task_address);
                                                                 return Result<void>::ok();
                                                             });
    }

    static Result<void> fail_task_visit(ThreadInfo& thread,
                                        EchionSampler& echion,
                                        PyThreadState* tstate,
                                        size_t& visit_count)
    {
        return thread.for_each_task_address(echion, tstate, [&visit_count](TaskObj*) -> Result<void> {
            ++visit_count;
            return ErrorKind::LocationError;
        });
    }

    static Result<std::vector<TaskInfo::Ptr>> get_all_tasks(ThreadInfo& thread,
                                                            EchionSampler& echion,
                                                            PyThreadState* tstate)
    {
        return thread.get_all_tasks(echion, tstate);
    }

    PyObject* globals = nullptr;
    PyObject* loop = nullptr;
    TaskObj* valid_task = nullptr;
    TaskObj* task = nullptr;
    EchionSampler echion;
    std::unique_ptr<ThreadInfo> thread;
#endif
};

#if PY_VERSION_HEX >= 0x030e0000
TEST_F(ThreadInfoTaskTraversalTest, SkipsTaskMovedToAnotherListWithoutPublishingAddresses)
{
    // Seed the output to verify a failed source does not publish addresses while preserving earlier results.
    std::vector<TaskObj*> task_addresses{ task };

    // Model Echion reading A and V from A <-> V <-> T before CPython moves T under head B. Reading T afterward
    // produces this mixed-time view:
    //
    //   copied nodes: A -> V -> T
    //   live task:              B <-> T
    //
    // Traversal reads V before T.prev != V reveals the malformed edge. It must not publish V.
    const llist_node original_valid_task_node = valid_task->task_node;
    const llist_node original_task_node = task->task_node;
    struct RemoteThreadState
    {
        uintptr_t padding[17];
        llist_node tasks_head;
    } remote_tstate{};
    llist_node& expected_head = remote_tstate.tasks_head;
    llist_node moved_head{};
    expected_head.next = &valid_task->task_node;
    expected_head.prev = &task->task_node;
    valid_task->task_node.prev = &expected_head;
    valid_task->task_node.next = &task->task_node;
    moved_head.next = moved_head.prev = &task->task_node;
    task->task_node.next = task->task_node.prev = &moved_head;
    thread->tstate_addr = reinterpret_cast<uintptr_t>(&remote_tstate);

    auto traversal = visit_thread_tasks(*thread, offsetof(RemoteThreadState, tasks_head), task_addresses);

    // Restore CPython's real links before assertions or object destruction can inspect them.
    valid_task->task_node = original_valid_task_node;
    task->task_node = original_task_node;

    EXPECT_TRUE(traversal);
    ASSERT_EQ(task_addresses.size(), 1);
    EXPECT_EQ(task_addresses.front(), task);
}

TEST_F(ThreadInfoTaskTraversalTest, PropagatesTaskAddressCallbackErrorsAndStopsTraversal)
{
    const llist_node original_task_node = task->task_node;
    const llist_node original_valid_task_node = valid_task->task_node;
    _PyThreadStateImpl remote_tstate{};
    llist_node& head = remote_tstate.asyncio_tasks_head;
    head.next = &task->task_node;
    head.prev = &valid_task->task_node;
    task->task_node.prev = &head;
    task->task_node.next = &valid_task->task_node;
    valid_task->task_node.prev = &task->task_node;
    valid_task->task_node.next = &head;
    thread->tstate_addr = reinterpret_cast<uintptr_t>(&remote_tstate);
    PyThreadState local_tstate{};
    size_t visit_count = 0;

    auto traversal = fail_task_visit(*thread, echion, &local_tstate, visit_count);

    // Restore CPython's real links before assertions or object destruction can inspect them.
    task->task_node = original_task_node;
    valid_task->task_node = original_valid_task_node;

    ASSERT_FALSE(traversal);
    EXPECT_EQ(traversal.error(), ErrorKind::LocationError);
    EXPECT_EQ(visit_count, 1);
}

TEST_F(ThreadInfoTaskTraversalTest, DeduplicatesTaskFoundInMultipleSources)
{
    // Expose the same Task through a valid thread list and the eager-task set.
    PyObject* eager_tasks = PySet_New(nullptr);
    ASSERT_NE(eager_tasks, nullptr);
    ASSERT_EQ(PySet_Add(eager_tasks, reinterpret_cast<PyObject*>(task)), 0);
    echion.init_asyncio(nullptr, eager_tasks);

    const llist_node original_task_node = task->task_node;
    _PyThreadStateImpl remote_tstate{};
    remote_tstate.asyncio_tasks_head.next = remote_tstate.asyncio_tasks_head.prev = &task->task_node;
    task->task_node.next = task->task_node.prev = &remote_tstate.asyncio_tasks_head;
    thread->tstate_addr = reinterpret_cast<uintptr_t>(&remote_tstate);
    PyThreadState local_tstate{};

    auto all_tasks = get_all_tasks(*thread, echion, &local_tstate);

    // Restore CPython's real links before assertions or object destruction can inspect them.
    task->task_node = original_task_node;
    Py_DECREF(eager_tasks);

    ASSERT_TRUE(all_tasks);
    EXPECT_EQ(all_tasks->size(), 1);
}
#endif
