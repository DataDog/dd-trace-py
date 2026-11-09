#pragma once

#include <cstddef>

// Byte offsets of the asyncio task-list heads in PyInterpreterState and _PyThreadStateImpl.
// 0 means unset or unavailable, and the task-list walkers then skip that list. A real offset is never 0 because
// asyncio_tasks_head is not the first member of either struct.
struct AsyncioOffsets
{
    size_t interpreter_tasks_head;
    size_t thread_tasks_head;
};
