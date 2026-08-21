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

// Mirrors the runtime table defined in CPython 3.14's Modules/_asynciomodule.c.
// TODO: Use more of these runtime offsets on Python 3.14+ to replace compile-time private-layout assumptions.
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

// Section discovery avoids depending on _asyncio's private module-state layout. Parsing still assumes that CPython
// preserves this debug table's layout within a minor version, matching CPython's remote-unwinding protocol.
inline std::optional<AsyncioOffsets>
parse_asyncio_debug_offsets(const PyAsyncioDebugOffsets* offsets)
{
    if (offsets == nullptr) {
        return std::nullopt;
    }

    constexpr uint64_t node_size = 2 * sizeof(uintptr_t);
    constexpr uint64_t max_size = std::numeric_limits<size_t>::max();
    const auto valid_field = [](uint64_t size, uint64_t field, uint64_t width, uint64_t alignment) {
        return size >= width && field <= size - width && field % alignment == 0 && field <= max_size;
    };

    // AsyncioDebug has no cookie, so validate the complete schema to reject false-positive section matches.
    if (!valid_field(offsets->task.size, offsets->task.task_name, sizeof(uintptr_t), alignof(uintptr_t)) ||
        !valid_field(offsets->task.size, offsets->task.task_awaited_by, sizeof(uintptr_t), alignof(uintptr_t)) ||
        !valid_field(offsets->task.size, offsets->task.task_is_task, sizeof(char), alignof(char)) ||
        !valid_field(offsets->task.size, offsets->task.task_awaited_by_is_set, sizeof(char), alignof(char)) ||
        !valid_field(offsets->task.size, offsets->task.task_coro, sizeof(uintptr_t), alignof(uintptr_t)) ||
        !valid_field(offsets->task.size, offsets->task.task_node, node_size, alignof(uintptr_t)) ||
        !valid_field(
          offsets->interpreter.size, offsets->interpreter.asyncio_tasks_head, node_size, alignof(uintptr_t)) ||
        !valid_field(
          offsets->thread.size, offsets->thread.asyncio_running_loop, sizeof(uintptr_t), alignof(uintptr_t)) ||
        !valid_field(
          offsets->thread.size, offsets->thread.asyncio_running_task, sizeof(uintptr_t), alignof(uintptr_t)) ||
        !valid_field(offsets->thread.size, offsets->thread.asyncio_tasks_head, node_size, alignof(uintptr_t))) {
        return std::nullopt;
    }

    return AsyncioOffsets{ static_cast<size_t>(offsets->interpreter.asyncio_tasks_head),
                           static_cast<size_t>(offsets->thread.asyncio_tasks_head) };
}

// Finds and parses the platform-specific AsyncioDebug binary section in the current process.
std::optional<AsyncioOffsets>
find_asyncio_debug_offsets();
