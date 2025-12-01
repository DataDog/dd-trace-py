#include "_memalloc_heap_map.hpp"
#include "_memalloc_debug.h"
#include "_memalloc_heap.h"

#include <memory>

// Free function defined in _memalloc_heap.cpp to return traceback to pool
extern void
heap_pool_put_traceback(std::unique_ptr<traceback_t> tb);

/* Note that the heap tracking tables will, in general, never free their backing
 * memory unless we completely clear them. Abseil's flat_hash_map uses approximately
 * 8 bytes for the key (void*), 8 bytes for the value (traceback_t*), plus metadata.
 * With a load factor of ~87.5% (Abseil's default), the table is quite efficient.
 * For our default maximum of 2^16 entries, the table will be roughly 2-3 MiB.
 * A table this large would correspond to a program with a ~65GiB live heap with
 * a 1MiB default sampling interval. Most of the memory usage of the profiler will
 * come from the tracebacks themselves, which we _do_ free when we're done with them.
 */

// memalloc_heap_map implementation
memalloc_heap_map::~memalloc_heap_map()
{
    // Delete all traceback objects before map is destroyed
    for (auto& [key, tb] : map) {
        delete tb;
    }
}

void
memalloc_heap_map::insert(void* key, traceback_t* value)
{
    // Try to insert the new value
    auto [it, inserted] = map.insert({ key, value });

    if (!inserted) {
        // Key already existed, replace the value
        /* This should not happen. It means we did not properly remove a previously-tracked
         * allocation from the map. Assert to detect if this edge case occurs in practice.
         * Export the previous entry if it hasn't been reported yet, then delete it and
         * replace it with the new value. */
        assert(false && "memalloc_heap_map::insert: found existing entry for key that should have been removed");
        traceback_t* prev = it->second;
        if (prev && !prev->reported) {
            /* If the sample hasn't been reported yet, export it before returning to pool */
            prev->sample.export_sample();
            prev->reported = true;
        }
        it->second = value;
        heap_pool_put_traceback(std::unique_ptr<traceback_t>(prev));
    }
}

traceback_t*
memalloc_heap_map::remove(void* key)
{
    auto it = map.find(key);
    if (it == map.end()) {
        return nullptr;
    }

    traceback_t* result = it->second;
    map.erase(it);
    return result;
}

void
memalloc_heap_map::destructive_copy_from(memalloc_heap_map& src)
{
    // Move all entries from src to this map using merge (C++17)
    // This efficiently transfers ownership without copying
    map.merge(src.map);

    // Clear any remaining entries in src (shouldn't be any after merge)
    src.map.clear();
}
