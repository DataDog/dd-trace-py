#pragma once

#include <cstddef>

// Byte offsets of the asyncio task-list heads in PyInterpreterState and _PyThreadStateImpl.
struct AsyncioOffsets
{
    size_t interpreter_tasks_head;
    size_t thread_tasks_head;
};
