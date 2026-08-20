#pragma once

#include <cstddef>
#include <cstdint>
#include <limits>
#include <optional>

struct AsyncioOffsets
{
    size_t interpreter_tasks_head;
    size_t thread_tasks_head;
};

// Mirrors the runtime table defined in CPython 3.14.2's Modules/_asynciomodule.c.
struct PyAsyncioDebugOffsets
{
    struct
    {
        uint64_t size;
        uint64_t task_name;
        uint64_t task_awaited_by;
        uint64_t task_is_task;
        uint64_t task_awaited_by_is_set;
        uint64_t task_coro;
        uint64_t task_node;
    } task;
    struct
    {
        uint64_t size;
        uint64_t asyncio_tasks_head;
    } interpreter;
    struct
    {
        uint64_t size;
        uint64_t asyncio_running_loop;
        uint64_t asyncio_running_task;
        uint64_t asyncio_tasks_head;
    } thread;
};

static_assert(sizeof(PyAsyncioDebugOffsets) == 13 * sizeof(uint64_t));

inline std::optional<AsyncioOffsets>
parse_asyncio_debug_offsets(const PyAsyncioDebugOffsets* offsets)
{
    if (offsets == nullptr) {
        return std::nullopt;
    }

    constexpr uint64_t node_size = 2 * sizeof(uintptr_t);
    constexpr uint64_t max_size = std::numeric_limits<size_t>::max();
    const auto valid_head = [](uint64_t size, uint64_t head) {
        return size >= node_size && head <= size - node_size && head % alignof(uintptr_t) == 0 && head <= max_size;
    };

    if (!valid_head(offsets->interpreter.size, offsets->interpreter.asyncio_tasks_head) ||
        !valid_head(offsets->thread.size, offsets->thread.asyncio_tasks_head)) {
        return std::nullopt;
    }

    return AsyncioOffsets{ static_cast<size_t>(offsets->interpreter.asyncio_tasks_head),
                           static_cast<size_t>(offsets->thread.asyncio_tasks_head) };
}
