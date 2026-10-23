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

struct AsyncioOffsets
{
    size_t interpreter_tasks_head;
    size_t thread_tasks_head;
    // Unset when the offsets do not come from a runtime table, for example in tests and the fuzz harness.
    std::optional<AsyncioTaskLayout> task_layout = std::nullopt;
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
// binary. Callers then skip the native task lists, which on 3.14 are the only record of asyncio.Task instances, so
// suspended tasks are not found; thread-stack sampling continues. Call during asyncio initialization, never on the
// sampling thread: Linux discovery reads files and may block.
//
// Discovery returns the first valid table in load order. It assumes the process loads a single CPython build that
// carries the table. If another 3.14 build's _asyncio, libpython, or executable were loaded before the active one,
// its table would also validate and its offsets, which describe that build's layout, would be returned instead.
std::optional<AsyncioOffsets>
find_asyncio_debug_offsets();

// Changes when binaries are loaded or unloaded. Discovery inspects only loaded binaries, so a failed discovery cannot
// succeed until this value changes. Returns no value when the platform cannot report changes; callers then retry.
std::optional<uint64_t>
loaded_binaries_generation();
