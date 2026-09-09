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
parse_asyncio_debug_offsets(const PyAsyncioDebugOffsets* offsets);

#if defined(__linux__)
struct dl_phdr_info;

// Reads section metadata from a borrowed regular-file descriptor, requiring its GNU build ID and program headers to
// match the loaded binary. Truncated, unsupported, or mismatched metadata returns no offsets.
std::optional<AsyncioOffsets>
read_asyncio_debug_offsets_from_elf(int fd, const dl_phdr_info& binary);
#endif

// Discovers the runtime table without caching failures. Linux requires readable ELF section headers and a GNU build ID;
// macOS uses the loaded Mach-O metadata. Missing metadata omits native task-list attribution, not thread stacks.
// Call during asyncio initialization, never from the sampling thread.
std::optional<AsyncioOffsets>
find_asyncio_debug_offsets();
