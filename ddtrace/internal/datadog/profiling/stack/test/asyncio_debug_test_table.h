#pragma once

#include <echion/cpython/asyncio_debug.h>

namespace asyncio_debug_test {

// A table that passes parse_asyncio_debug_offsets. Tests change one field at a time.
inline PyAsyncioDebugOffsets
valid_table()
{
    PyAsyncioDebugOffsets table{};
    table.task.size = 512;
    table.task.task_name = 8;
    table.task.task_awaited_by = 16;
    table.task.task_is_task = 24;
    table.task.task_awaited_by_is_set = 25;
    table.task.task_coro = 32;
    table.task.task_node = 40;
    table.interpreter.size = 4096;
    table.interpreter.asyncio_tasks_head = 128;
    table.thread.size = 1024;
    table.thread.asyncio_running_loop = 8;
    table.thread.asyncio_running_task = 16;
    table.thread.asyncio_tasks_head = 256;
    return table;
}

} // namespace asyncio_debug_test
