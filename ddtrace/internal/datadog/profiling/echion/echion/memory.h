// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#include <Python.h>

#include <optional>
#include <unordered_map>

#include <sys/resource.h>

#include <echion/config.h>
#include <echion/interp.h>
#include <echion/mojo.h>
#include <echion/stacks.h>
#include <echion/threads.h>

// ----------------------------------------------------------------------------
class ResidentMemoryTracker
{
public:
    size_t size;

    // ------------------------------------------------------------------------
    ResidentMemoryTracker()
    {
        update();
    }

    // ------------------------------------------------------------------------
    bool inline check()
    {
        size_t old_size = size;
        update();
        return size != old_size;
    }

private:
    // ------------------------------------------------------------------------
    void inline update()
    {
        struct rusage usage;
        getrusage(RUSAGE_SELF, &usage);
        size = usage.ru_maxrss;
    }
};

inline ResidentMemoryTracker rss_tracker;

// ----------------------------------------------------------------------------

class MemoryStats
{
public:
    int64_t iid;
    std::string thread_name;

    FrameStack::Key stack;

    size_t count;
    ssize_t size;

    // ------------------------------------------------------------------------
    MemoryStats(int iid, std::string thread_name, FrameStack::Key stack, size_t count, size_t size)
        : iid(iid), thread_name(thread_name), stack(stack), count(count), size(size)
    {
    }

    // ------------------------------------------------------------------------
    void inline render()
    {
        Renderer::get().render_stack_begin(pid, iid, thread_name);

        stack_table.retrieve(stack).render();

        Renderer::get().render_stack_end(MetricType::Memory, size);
    }
};

// ----------------------------------------------------------------------------
struct MemoryTableEntry
{
    FrameStack::Key stack;
    size_t size;
};

// ----------------------------------------------------------------------------
class MemoryTable : public std::unordered_map<void*, MemoryTableEntry>
{
public:
    // ------------------------------------------------------------------------
    void link(void* address, FrameStack::Key stack, size_t size)
    {
        std::lock_guard<std::mutex> lock(this->lock);

        this->emplace(address, MemoryTableEntry{stack, size});
    }

    // ------------------------------------------------------------------------
    std::optional<MemoryTableEntry> unlink(void* address)
    {
        std::lock_guard<std::mutex> lock(this->lock);

        auto it = this->find(address);

        if (it != this->end())
        {
            auto entry = it->second;
            erase(it);
            return {entry};
        }

        return {};
    }

private:
    std::mutex lock;
};

// ----------------------------------------------------------------------------
class StackStats
{
public:
    // ------------------------------------------------------------------------
    void inline update(PyThreadState* tstate, FrameStack::Key stack, size_t size)
    {
        std::lock_guard<std::mutex> lock(this->lock);

        auto stack_entry = map.find(stack);

        if (stack_entry == map.end())
        {
            if (tstate == NULL)
                // Invalid thread state, nothing we can do.
                return;

            std::lock_guard<std::mutex> ti_lock(thread_info_map_lock);

            // Map the memory address with the stack so that we can account for
            // the deallocations.
            map.emplace(stack,
                        MemoryStats(tstate->interp->id, thread_info_map[tstate->thread_id]->name,
                                    stack, 1, size));
        }
        else
        {
            stack_entry->second.count++;
            stack_entry->second.size += size;
        }
    }

    // ------------------------------------------------------------------------
    void inline update(MemoryTableEntry& entry)
    {
        std::lock_guard<std::mutex> lock(this->lock);

        auto stack_entry = map.find(entry.stack);

        if (stack_entry != map.end())
            stack_entry->second.size -= entry.size;
    }

    // ------------------------------------------------------------------------
    void flush()
    {
        std::lock_guard<std::mutex> lock(this->lock);

        for (auto& entry : map)
        {
            // Emit non-trivial stack stats only
            if (entry.second.size != 0)
                entry.second.render();

            // Reset the stats
            entry.second.size = 0;
            entry.second.count = 0;
        }
    }

    // ------------------------------------------------------------------------
    void clear()
    {
        std::lock_guard<std::mutex> lock(this->lock);

        map.clear();
    }

private:
    std::mutex lock;
    std::unordered_map<FrameStack::Key, MemoryStats> map;
};

// ----------------------------------------------------------------------------

// We make this a reference to a heap-allocated object so that we can avoid
// the destruction on exit. We are in charge of cleaning up the object. Note
// that the object will leak, but this is not a problem.
inline auto& stack_stats = *(new StackStats());
inline auto& memory_table = *(new MemoryTable());

// ----------------------------------------------------------------------------
static inline void general_alloc(void* address, size_t size)
{
    auto stack = std::make_unique<FrameStack>();
    auto* tstate = PyThreadState_Get();  // DEV: This should be called with the GIL held

    // DEV: We unwind the stack by reading the data out of live Python objects.
    // This works under the assumption that the objects/data structures we are
    // interested in belong to the thread whose stack we are unwinding.
    // Therefore, we expect these structures to remain valid and essentially
    // immutable for the duration of the unwinding process, which happens
    // in-line with the allocation within the calling thread.
    unwind_python_stack_unsafe(tstate, *stack);

    // Store the stack and get its key for reference
    // TODO: Handle collision exception
    auto stack_key = stack_table.store(std::move(stack));

    // Link the memory address with the stack
    memory_table.link(address, stack_key, size);

    // Update the stack stats
    stack_stats.update(tstate, stack_key, size);
}

// ----------------------------------------------------------------------------
static inline void general_free(void* address)
{
    // Retrieve the stack that made the allocation
    if (auto entry = memory_table.unlink(address))
        // Update the stack stats
        stack_stats.update(*entry);
}

// ----------------------------------------------------------------------------
static void* echion_malloc(void* ctx, size_t n)
{
    auto* alloc = (PyMemAllocatorEx*)ctx;

    // Make the actual allocation
    auto address = alloc->malloc(alloc->ctx, n);

    // Handle the allocation event
    if (address != NULL)
        general_alloc(address, n);

    return address;
}

// ----------------------------------------------------------------------------
static void* echion_calloc(void* ctx, size_t nelem, size_t elsize)
{
    auto* alloc = (PyMemAllocatorEx*)ctx;

    // Make the actual allocation
    auto address = alloc->calloc(alloc->ctx, nelem, elsize);

    // Handle the allocation event
    if (address != NULL)
        general_alloc(address, nelem * elsize);

    return address;
}

// ----------------------------------------------------------------------------
static void* echion_realloc(void* ctx, void* p, size_t n)
{
    auto* alloc = (PyMemAllocatorEx*)ctx;

    // Model this as a deallocation followed by an allocation
    if (p != NULL)
        general_free(p);

    auto address = alloc->realloc(alloc->ctx, p, n);

    if (address != NULL)
        general_alloc(address, n);

    return address;
}

// ----------------------------------------------------------------------------
static void echion_free(void* ctx, void* p)
{
    auto* alloc = (PyMemAllocatorEx*)ctx;

    // Handle the deallocation event
    if (p != NULL)
        general_free(p);

    alloc->free(alloc->ctx, p);
}

// ----------------------------------------------------------------------------

// DEV: We define this macro on the basis of the knowledge that the domains are
//      defined as an enum.
#define ALLOC_DOMAIN_COUNT 3

inline PyMemAllocatorEx original_allocators[ALLOC_DOMAIN_COUNT];
inline PyMemAllocatorEx echion_allocator = {NULL, echion_malloc, echion_calloc, echion_realloc,
                                            echion_free};

// ----------------------------------------------------------------------------
static void setup_memory()
{
    for (int i = 0; i < ALLOC_DOMAIN_COUNT; i++)
    {
        // Save the original allocators
        PyMem_GetAllocator(static_cast<PyMemAllocatorDomain>(i), &original_allocators[i]);

        // Install the new allocators
        echion_allocator.ctx = (void*)&original_allocators[i];
        PyMem_SetAllocator(static_cast<PyMemAllocatorDomain>(i), &echion_allocator);
    }
}

// ----------------------------------------------------------------------------
static void teardown_memory()
{
    // Restore the original allocators
    for (int i = 0; i < ALLOC_DOMAIN_COUNT; i++)
        PyMem_SetAllocator(static_cast<PyMemAllocatorDomain>(i), &original_allocators[i]);

    stack_stats.flush();

    stack_stats.clear();
    stack_table.clear();
    memory_table.clear();
}
