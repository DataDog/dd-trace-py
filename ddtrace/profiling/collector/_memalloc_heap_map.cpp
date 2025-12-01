#include "_memalloc_heap_map.hpp"
#include "_memalloc_debug.h"

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

traceback_t*
memalloc_heap_map::insert(void* key, traceback_t* value)
{
    // Try to insert the new value
    auto [it, inserted] = map.insert({ key, value });

    if (!inserted) {
        // Key already existed, replace the value
        /* This should not happen. It means we did not properly remove a previously-tracked
         * allocation from the map. This should probably be an assertion. Return the previous
         * entry as it is for an allocation that has been freed. */
        traceback_t* prev = it->second;
        it->second = value;
        return prev;
    }

    return nullptr;
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

PyObject*
memalloc_heap_map::export_to_python() const
{
    PyObject* heap_list = PyList_New(map.size());
    if (heap_list == nullptr) {
        return nullptr;
    }

    size_t i = 0;
    for (const auto& [key, tb] : map) {
        PyObject* tb_and_size = PyTuple_New(2);
        PyTuple_SET_ITEM(tb_and_size, 0, tb->to_tuple());
        PyTuple_SET_ITEM(tb_and_size, 1, PyLong_FromSize_t(tb->size));
        PyList_SET_ITEM(heap_list, i++, tb_and_size);

        memalloc_debug_gil_release();
    }
    return heap_list;
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
