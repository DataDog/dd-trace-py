// Platform-independent AsyncioDebug tests: table validation, and discovery of the real CPython runtime table.
// Platform readers have their own tests in test_asyncio_debug_linux.cpp and test_asyncio_debug_macos.cpp.

#include "asyncio_debug_test_table.h"

#include "echion/echion_sampler.h"

#include <gtest/gtest.h>

using asyncio_debug_test::valid_table;

// ----------------------------------------------------------------------------
// Table validation (parse_asyncio_debug_offsets).
// ----------------------------------------------------------------------------

TEST(AsyncioDebugOffsets, ValidatesAndStoresTaskListHeads)
{
    auto table = valid_table();
    auto offsets = parse_asyncio_debug_offsets(table);
    ASSERT_TRUE(offsets);

    EchionSampler echion;
    echion.set_asyncio_offsets(*offsets);
    EXPECT_EQ(echion.asyncio_interpreter_tasks_head_offset(), 128);
    EXPECT_EQ(echion.asyncio_thread_tasks_head_offset(), 256);
}

TEST(AsyncioDebugOffsets, ReportsTaskLayout)
{
    auto table = valid_table();
    auto offsets = parse_asyncio_debug_offsets(table);
    ASSERT_TRUE(offsets);
    ASSERT_TRUE(offsets->task_layout);
    EXPECT_EQ(*offsets->task_layout,
              (AsyncioTaskLayout{ table.task.size,
                                  table.task.task_name,
                                  table.task.task_awaited_by,
                                  table.task.task_is_task,
                                  table.task.task_awaited_by_is_set,
                                  table.task.task_coro,
                                  table.task.task_node }));
}

TEST(AsyncioDebugOffsets, RejectsTaskNodeOutsideTask)
{
    auto table = valid_table();
    table.task.task_node = table.task.size;
    EXPECT_FALSE(parse_asyncio_debug_offsets(table));
}

TEST(AsyncioDebugOffsets, RejectsThreadHeadOutsideThread)
{
    auto table = valid_table();
    table.thread.asyncio_tasks_head = table.thread.size;
    EXPECT_FALSE(parse_asyncio_debug_offsets(table));
}

TEST(AsyncioDebugOffsets, RejectsZeroInterpreterHead)
{
    auto table = valid_table();
    table.interpreter.asyncio_tasks_head = 0;
    EXPECT_FALSE(parse_asyncio_debug_offsets(table));
}

TEST(AsyncioDebugOffsets, RejectsZeroThreadHead)
{
    auto table = valid_table();
    table.thread.asyncio_tasks_head = 0;
    EXPECT_FALSE(parse_asyncio_debug_offsets(table));
}

TEST(AsyncioDebugOffsets, RejectsMisalignedTaskCoroutine)
{
    auto table = valid_table();
    ++table.task.task_coro;
    EXPECT_FALSE(parse_asyncio_debug_offsets(table));
}

TEST(AsyncioDebugOffsets, RejectsInterpreterTooSmallForListHead)
{
    auto table = valid_table();
    table.interpreter.size = sizeof(uintptr_t);
    EXPECT_FALSE(parse_asyncio_debug_offsets(table));
}

TEST(AsyncioDebugOffsets, RejectsMisalignedInterpreterHead)
{
    auto table = valid_table();
    ++table.interpreter.asyncio_tasks_head;
    EXPECT_FALSE(parse_asyncio_debug_offsets(table));
}

TEST(AsyncioDebugOffsets, RejectsMisalignedThreadHead)
{
    auto table = valid_table();
    ++table.thread.asyncio_tasks_head;
    EXPECT_FALSE(parse_asyncio_debug_offsets(table));
}

// ----------------------------------------------------------------------------
// Task layout check against Echion's TaskObj mirror.
// ----------------------------------------------------------------------------

#if PY_VERSION_HEX >= 0x030e0000
namespace {

AsyncioTaskLayout
mirror_task_layout()
{
    return AsyncioTaskLayout{ sizeof(TaskObj),
                              offsetof(TaskObj, task_name),
                              offsetof(TaskObj, task_awaited_by),
                              offsetof(TaskObj, task_is_task),
                              offsetof(TaskObj, task_awaited_by_is_set),
                              offsetof(TaskObj, task_coro),
                              offsetof(TaskObj, task_node) };
}

} // namespace

TEST(AsyncioDebugOffsets, AcceptsTaskLayoutMatchingMirror)
{
    EXPECT_TRUE(EchionSampler::asyncio_task_layout_matches_mirror(mirror_task_layout()));
}

TEST(AsyncioDebugOffsets, RejectsTaskLayoutDifferingFromMirror)
{
    auto layout = mirror_task_layout();
    layout.task_node += sizeof(uintptr_t);
    EXPECT_FALSE(EchionSampler::asyncio_task_layout_matches_mirror(layout));

    layout = mirror_task_layout();
    layout.size += sizeof(uintptr_t);
    EXPECT_FALSE(EchionSampler::asyncio_task_layout_matches_mirror(layout));
}
#endif

// ----------------------------------------------------------------------------
// Discovery of the real runtime table.
// ----------------------------------------------------------------------------

#if PY_VERSION_HEX >= 0x030e0000
TEST(AsyncioDebugOffsets, DiscoversRuntimeTableAfterAnEarlierAttempt)
{
    Py_Initialize();
    // Shared _asyncio builds have no table yet. Built-in _asyncio may already expose it before import.
    (void)find_asyncio_debug_offsets();
    PyObject* module = PyImport_ImportModule("asyncio");
    ASSERT_NE(module, nullptr);
    auto offsets = find_asyncio_debug_offsets();
    Py_DECREF(module);
    ASSERT_TRUE(offsets);
    // Native tests use the same interpreter and headers, unlike wheels running on a different patch release.
    EXPECT_EQ(offsets->interpreter_tasks_head, offsetof(PyInterpreterState, asyncio_tasks_head));
    EXPECT_EQ(offsets->thread_tasks_head, offsetof(_PyThreadStateImpl, asyncio_tasks_head));
    ASSERT_TRUE(offsets->task_layout);
    EXPECT_TRUE(EchionSampler::asyncio_task_layout_matches_mirror(*offsets->task_layout));
}

TEST(AsyncioDebugOffsets, LoadedBinariesGenerationIsStableWithoutLoads)
{
    const auto before = loaded_binaries_generation();
    ASSERT_TRUE(before);
    EXPECT_EQ(loaded_binaries_generation(), before);
}
#endif
