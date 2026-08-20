#include "echion/echion_sampler.h"
#include "echion/threads.h"

#include <gtest/gtest.h>

class ThreadInfoTaskTraversalTest : public ::testing::Test
{
  protected:
#if PY_VERSION_HEX >= 0x030e0000
    static Result<void>
    traverse(ThreadInfo& thread, EchionSampler& echion, uintptr_t head, std::vector<TaskInfo::Ptr>& tasks)
    {
        return thread.get_tasks_from_linked_list(echion, head, tasks);
    }
#endif
};

#if PY_VERSION_HEX >= 0x030e0000
TEST_F(ThreadInfoTaskTraversalTest, RejectsTaskMovedToAnotherList)
{
    Py_Initialize();
    PyObject* globals = PyDict_New();
    ASSERT_NE(globals, nullptr);
    ASSERT_EQ(PyDict_SetItemString(globals, "__builtins__", PyEval_GetBuiltins()), 0);

    PyObject* result = PyRun_String(R"(
import asyncio
loop = asyncio.new_event_loop()
async def wait_forever():
    await asyncio.Event().wait()
task = loop.create_task(wait_forever())
)",
                                    Py_file_input,
                                    globals,
                                    globals);
    ASSERT_NE(result, nullptr);
    Py_DECREF(result);

    auto* loop = PyDict_GetItemString(globals, "loop");
    auto* task = reinterpret_cast<TaskObj*>(PyDict_GetItemString(globals, "task"));
    ASSERT_NE(loop, nullptr);
    ASSERT_NE(task, nullptr);

    EchionSampler echion;
#if defined PL_LINUX
    ThreadInfo thread(1, 1, "test-thread", CLOCK_THREAD_CPUTIME_ID);
#elif defined PL_DARWIN
    ThreadInfo thread(1, 1, "test-thread", mach_thread_self());
#endif
    thread.asyncio_loop = reinterpret_cast<uintptr_t>(loop);

    std::vector<TaskInfo::Ptr> tasks;
    auto maybe_task = TaskInfo::create(echion, task);
    ASSERT_TRUE(maybe_task);
    tasks.push_back(std::move(*maybe_task));

    const llist_node original_task_node = task->task_node;
    llist_node expected_head{};
    llist_node moved_head{};
    expected_head.next = expected_head.prev = &task->task_node;
    moved_head.next = moved_head.prev = &task->task_node;
    task->task_node.next = task->task_node.prev = &moved_head;

    result = nullptr;
    auto traversal = traverse(thread, echion, reinterpret_cast<uintptr_t>(&expected_head), tasks);

    EXPECT_FALSE(traversal);
    EXPECT_EQ(tasks.size(), 1);

    task->task_node = original_task_node;
    tasks.clear();
    result = PyRun_String(R"(
task.cancel()
try:
    loop.run_until_complete(task)
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
