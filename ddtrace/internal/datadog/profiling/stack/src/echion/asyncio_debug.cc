// Validation of CPython's AsyncioDebug table (Python 3.14+), independent of the Python version this is built for.
// Every failure returns no offsets, which disables the native task lists but not thread-stack sampling.

#include <echion/cpython/asyncio_debug.h>
#include <echion/vm.h>

#include <limits>

std::optional<AsyncioOffsets>
parse_asyncio_debug_offsets(const PyAsyncioDebugOffsets& offsets)
{
    // Size of struct llist_node (next and prev pointers), the type of every task_node and asyncio_tasks_head field.
    constexpr uint64_t node_size = 2 * sizeof(uintptr_t);
    constexpr uint64_t max_size = std::numeric_limits<size_t>::max();
    // Ordered so size - width cannot underflow. The last check rejects values that do not fit size_t on 32-bit builds.
    const auto valid_field = [](uint64_t size, uint64_t field, uint64_t width, uint64_t alignment) {
        return size >= width && field <= size - width && field % alignment == 0 && field <= max_size;
    };

    if (offsets.interpreter.asyncio_tasks_head == 0 || offsets.thread.asyncio_tasks_head == 0 ||
        !valid_field(offsets.task.size, offsets.task.task_name, sizeof(uintptr_t), alignof(uintptr_t)) ||
        !valid_field(offsets.task.size, offsets.task.task_awaited_by, sizeof(uintptr_t), alignof(uintptr_t)) ||
        !valid_field(offsets.task.size, offsets.task.task_is_task, sizeof(char), alignof(char)) ||
        !valid_field(offsets.task.size, offsets.task.task_awaited_by_is_set, sizeof(char), alignof(char)) ||
        !valid_field(offsets.task.size, offsets.task.task_coro, sizeof(uintptr_t), alignof(uintptr_t)) ||
        !valid_field(offsets.task.size, offsets.task.task_node, node_size, alignof(uintptr_t)) ||
        !valid_field(offsets.interpreter.size, offsets.interpreter.asyncio_tasks_head, node_size, alignof(uintptr_t)) ||
        !valid_field(offsets.thread.size, offsets.thread.asyncio_running_loop, sizeof(uintptr_t), alignof(uintptr_t)) ||
        !valid_field(offsets.thread.size, offsets.thread.asyncio_running_task, sizeof(uintptr_t), alignof(uintptr_t)) ||
        !valid_field(offsets.thread.size, offsets.thread.asyncio_tasks_head, node_size, alignof(uintptr_t))) {
        return std::nullopt;
    }

    return AsyncioOffsets{ static_cast<size_t>(offsets.interpreter.asyncio_tasks_head),
                           static_cast<size_t>(offsets.thread.asyncio_tasks_head),
                           AsyncioTaskLayout{ offsets.task.size,
                                              offsets.task.task_name,
                                              offsets.task.task_awaited_by,
                                              offsets.task.task_is_task,
                                              offsets.task.task_awaited_by_is_set,
                                              offsets.task.task_coro,
                                              offsets.task.task_node } };
}

std::optional<AsyncioOffsets>
read_asyncio_debug_offsets_at(uintptr_t table_address, const AsyncioTaskLayout& expected_task_layout)
{
    if (table_address == 0 || table_address % alignof(uint64_t) != 0) {
        return std::nullopt;
    }

    PyAsyncioDebugOffsets table{};
    if (!read_own_memory(table_address, &table, sizeof(table))) {
        return std::nullopt;
    }

    auto offsets = parse_asyncio_debug_offsets(table);
    if (!offsets || offsets->task_layout != expected_task_layout) {
        return std::nullopt;
    }
    return offsets;
}
