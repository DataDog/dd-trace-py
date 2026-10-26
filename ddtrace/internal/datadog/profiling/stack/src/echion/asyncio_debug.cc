// Validation of CPython's AsyncioDebug table (Python 3.14+), independent of the Python version this is built for.
//
// Echion walks two private linked lists of asyncio tasks: one rooted in PyInterpreterState and one in each
// _PyThreadStateImpl. Their offsets can change between 3.14 patch releases when unrelated fields earlier in those
// structs change, so a wheel built against one patch release cannot rely on compile-time offsets. CPython's _asyncio
// module publishes them in a 13-field table, and its module state points to that table. asyncio_discovery.cc finds
// the pointer; this file reads the table through the kernel, validates it, and checks the offsets against live list
// heads before anything is published. Every failure returns no offsets, which disables the native task lists but not
// thread-stack sampling.

#include <echion/cpython/asyncio_debug.h>

#include <limits>

#if defined(__linux__)
#include <sys/uio.h>
#include <unistd.h>
#elif defined(__APPLE__)
#include <mach/mach.h>
#include <mach/mach_vm.h>
#endif

std::optional<AsyncioOffsets>
parse_asyncio_debug_offsets(const PyAsyncioDebugOffsets& offsets)
{
    // Size of struct llist_node (next and prev pointers), the type of every task_node and asyncio_tasks_head field.
    constexpr uint64_t node_size = 2 * sizeof(uintptr_t);
    constexpr uint64_t max_size = std::numeric_limits<size_t>::max();
    // A field of the given width and alignment fits in a struct of the given size:
    // - size >= width comes first, so size - width cannot underflow.
    // - field <= size - width keeps the whole field inside the struct.
    // - field % alignment rejects values that cannot be the offset of a field of that type.
    // - field <= max_size rejects values that do not fit size_t (32-bit builds).
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

bool
read_own_memory(uintptr_t address, void* buffer, size_t size)
{
    if (address == 0 || size == 0) {
        return false;
    }

#if defined(__linux__)
    struct iovec local = { buffer, size };
    // The kernel copies from this address; it is never dereferenced here.
    struct iovec remote = { reinterpret_cast<void*>(address), size }; // NOLINT(performance-no-int-to-ptr)
    return process_vm_readv(getpid(), &local, 1, &remote, 1, 0) == static_cast<ssize_t>(size);
#elif defined(__APPLE__)
    mach_vm_size_t copied = 0;
    return mach_vm_read_overwrite(
             mach_task_self(), address, size, reinterpret_cast<mach_vm_address_t>(buffer), &copied) == KERN_SUCCESS &&
           copied == size;
#else
    (void)buffer;
    return false;
#endif
}

std::optional<AsyncioOffsets>
read_asyncio_debug_offsets_at(uintptr_t table_address,
                              const AsyncioTaskLayout& expected_task_layout,
                              AsyncioReadMemory read)
{
    if (table_address == 0 || table_address % alignof(uint64_t) != 0) {
        return std::nullopt;
    }

    PyAsyncioDebugOffsets table{};
    if (!read(table_address, &table, sizeof(table))) {
        return std::nullopt;
    }

    auto offsets = parse_asyncio_debug_offsets(table);
    // Task unwinding reads TaskObj through Echion's compile-time mirror. A runtime whose table disagrees with it would
    // make the list walkers misread every task, so such a table is rejected along with its list-head offsets.
    if (!offsets || offsets->task_layout != expected_task_layout) {
        return std::nullopt;
    }
    return offsets;
}

namespace {

// Same layout as CPython's struct llist_node, declared here so this file does not depend on the Python version.
struct ListNode
{
    uintptr_t next;
    uintptr_t prev;
};

} // namespace

bool
asyncio_list_head_is_consistent(uintptr_t head_address, AsyncioReadMemory read)
{
    ListNode head{};
    if (!read(head_address, &head, sizeof(head)) || head.next == 0 || head.prev == 0) {
        return false;
    }

    ListNode next{};
    ListNode prev{};
    return read(head.next, &next, sizeof(next)) && read(head.prev, &prev, sizeof(prev)) && next.prev == head_address &&
           prev.next == head_address;
}

bool
asyncio_offsets_match_live_heads(const AsyncioOffsets& offsets,
                                 uintptr_t thread_base,
                                 uintptr_t interp_base,
                                 AsyncioReadMemory read)
{
    const auto head_matches = [read](uintptr_t base, size_t offset) {
        return base != 0 && offset <= std::numeric_limits<uintptr_t>::max() - base &&
               asyncio_list_head_is_consistent(base + offset, read);
    };

    if (!head_matches(thread_base, offsets.thread_tasks_head)) {
        return false;
    }
    return interp_base == 0 || head_matches(interp_base, offsets.interpreter_tasks_head);
}
