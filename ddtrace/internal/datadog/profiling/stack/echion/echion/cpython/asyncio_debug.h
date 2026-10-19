#pragma once

#include <cstddef>
#include <cstdint>
#include <optional>

struct AsyncioOffsets
{
    size_t interpreter_tasks_head;
    size_t thread_tasks_head;
};

// Mirrors the runtime table defined in CPython 3.14.7's Modules/_asynciomodule.c.
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
std::optional<AsyncioOffsets>
parse_asyncio_debug_offsets(const PyAsyncioDebugOffsets& offsets);

#if defined(__linux__)
struct dl_phdr_info;

// Reads section metadata from a borrowed regular-file descriptor, requiring its program headers to match the loaded
// binary. A loaded GNU build ID must match when present; build-ID-free objects require the table's file bytes to equal
// the loaded bytes, independent of filesystem identity. Truncated, unsupported, or mismatched metadata returns no
// offsets.
std::optional<AsyncioOffsets>
read_asyncio_debug_offsets_from_elf(int fd, const dl_phdr_info& binary);
#endif

// Finds and validates CPython's AsyncioDebug table in the loaded binaries (implemented in asyncio_debug_linux.cc and
// asyncio_debug_macos.cc). Returns no offsets when the table is missing or cannot be proven to belong to a loaded
// binary; callers then omit native task-list attribution but keep thread-stack sampling. Call during asyncio
// initialization, never on the sampling thread: Linux discovery reads files and may block.
std::optional<AsyncioOffsets>
find_asyncio_debug_offsets();
