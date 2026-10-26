#pragma once

#include <cstddef>
#include <cstdint>
#include <optional>

// TaskObj field offsets published by the runtime, compared against Echion's compile-time TaskObj mirror.
struct AsyncioTaskLayout
{
    uint64_t size;
    uint64_t task_name;
    uint64_t task_awaited_by;
    uint64_t task_is_task;
    uint64_t task_awaited_by_is_set;
    uint64_t task_coro;
    uint64_t task_node;

    bool operator==(const AsyncioTaskLayout&) const = default;
};

// Byte offsets of the asyncio task-list heads in PyInterpreterState and _PyThreadStateImpl.
// 0 means unset or unavailable, and the task-list walkers then skip that list. A real offset is never 0 because
// asyncio_tasks_head is not the first member of either struct.
struct AsyncioOffsets
{
    size_t interpreter_tasks_head;
    size_t thread_tasks_head;
    // Unset when the offsets do not come from a runtime table, for example in tests and the fuzz harness.
    std::optional<AsyncioTaskLayout> task_layout = std::nullopt;
};

// Mirrors Py_AsyncioModuleDebugOffsets in Modules/_asynciomodule.c, unchanged from CPython 3.14.0 through 3.15.0b1.
// CPython publishes it for out-of-process debuggers; _asyncio's module state points to it.
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

// Validates every field of the table and returns the two list-head offsets with the task layout. The table has no
// magic value, so a full schema check is what rejects memory that is not a table.
std::optional<AsyncioOffsets>
parse_asyncio_debug_offsets(const PyAsyncioDebugOffsets& offsets);

// Copies size bytes of this process's memory at address into buffer. Returns false, without faulting, when any part
// of the range is unreadable.
using AsyncioReadMemory = bool (*)(const void* address, void* buffer, size_t size);

// Reads this process's memory through the kernel (process_vm_readv on Linux, mach_vm_read_overwrite on macOS). Not
// Echion's copy_memory: the sampler switches that between strategies while it runs, and its safe_memcpy strategy
// depends on a signal handler that faulthandler and crashtracking swap out while the sampler, but not this caller, is
// paused. A kernel read is also free of C++ data races on memory other threads write.
bool
read_own_memory(const void* address, void* buffer, size_t size);

// Reads and validates the table at table_address. Returns no offsets for a null, misaligned or unreadable address, a
// table that fails parse_asyncio_debug_offsets, or a task layout different from expected_task_layout.
std::optional<AsyncioOffsets>
read_asyncio_debug_offsets_at(uintptr_t table_address,
                              const AsyncioTaskLayout& expected_task_layout,
                              AsyncioReadMemory read);

// True when head_address holds a readable, self-consistent circular list head: head->next->prev == head and
// head->prev->next == head. An empty list is self-linked and passes.
bool
asyncio_list_head_is_consistent(uintptr_t head_address, AsyncioReadMemory read);

// Checks the offsets against live list heads: the thread head always, the interpreter head only when interp_base is
// non-zero. Callers pass 0 on free-threaded builds, where another thread can be moving tasks into the interpreter list
// at the same time. Rejects a base + offset that overflows.
bool
asyncio_offsets_match_live_heads(const AsyncioOffsets& offsets,
                                 uintptr_t thread_base,
                                 uintptr_t interp_base,
                                 AsyncioReadMemory read);
