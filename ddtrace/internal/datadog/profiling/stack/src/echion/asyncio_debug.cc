// Runtime discovery of CPython's AsyncioDebug table (Python 3.14+).
//
// Echion walks two private linked lists of asyncio tasks: one rooted in PyInterpreterState and one in each
// _PyThreadStateImpl. Their offsets can change between 3.14 patch releases when unrelated fields earlier in those
// structs change, so a wheel built against one patch release cannot rely on compile-time offsets. CPython's _asyncio
// module publishes them in a 13-field table, placed in a dedicated section for out-of-process debuggers:
// .AsyncioDebug on ELF and __DATA,AsyncioDebug on Mach-O.
//
// Discovery has two steps:
//   1. Locate the table in a loaded binary. This is platform specific:
//      - asyncio_debug_linux.cc (ELF): section headers are not mapped into memory, so they are read from the
//        binary's file, which must then be proven to be the binary that was loaded.
//      - asyncio_debug_macos.cc (Mach-O): load commands are mapped, so everything is read from memory.
//   2. Validate the whole table and return the two list-head offsets (parse_asyncio_debug_offsets, below).
//
// Both platforms read loaded memory with direct syscalls and fail closed: missing, unreadable, or inconsistent
// metadata yields no offsets, which disables native task-list attribution but not thread-stack sampling.

#include <echion/cpython/asyncio_debug.h>

#include <limits>

std::optional<AsyncioOffsets>
parse_asyncio_debug_offsets(const PyAsyncioDebugOffsets& offsets)
{
    constexpr uint64_t node_size = 2 * sizeof(uintptr_t);
    constexpr uint64_t max_size = std::numeric_limits<size_t>::max();
    const auto valid_field = [](uint64_t size, uint64_t field, uint64_t width, uint64_t alignment) {
        return size >= width && field <= size - width && field % alignment == 0 && field <= max_size;
    };

    // AsyncioDebug has no cookie, so validate the complete schema to reject false-positive section matches.
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
                           static_cast<size_t>(offsets.thread.asyncio_tasks_head) };
}
