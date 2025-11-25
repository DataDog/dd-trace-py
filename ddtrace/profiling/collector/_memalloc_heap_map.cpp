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
memalloc_heap_map::memalloc_heap_map() = default;

memalloc_heap_map::~memalloc_heap_map()
{
    // Delete all traceback objects before map is destroyed
    for (auto& pair : map) {
        delete pair.second;
    }
}

size_t
memalloc_heap_map::size() const
{
    return map.size();
}

traceback_t*
memalloc_heap_map::insert(void* key, traceback_t* value)
{
    traceback_t* prev = nullptr;

    // Try to insert the new value
    auto result = map.insert({ key, value });

    if (!result.second) {
        // Key already existed, replace the value
        /* This should not happen. It means we did not properly remove a previously-tracked
         * allocation from the map. This should probably be an assertion. Return the previous
         * entry as it is for an allocation that has been freed. */
        prev = result.first->second;
        result.first->second = value;
    }

    return prev;
}

bool
memalloc_heap_map::contains(void* key) const
{
    return map.find(key) != map.end();
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

    int i = 0;
    for (const auto& pair : map) {
        traceback_t* tb = pair.second;

        PyObject* tb_and_size = PyTuple_New(2);
        PyTuple_SET_ITEM(tb_and_size, 0, tb->to_tuple());
        PyTuple_SET_ITEM(tb_and_size, 1, PyLong_FromSize_t(tb->size));
        PyList_SET_ITEM(heap_list, i, tb_and_size);
        i++;

        memalloc_debug_gil_release();
    }
    return heap_list;
}

void
memalloc_heap_map::destructive_copy_from(memalloc_heap_map& src)
{
    // Move all entries from src to this map
    for (auto& pair : src.map) {
        map.insert(pair);
    }

    // Clear the source map without deleting the tracebacks
    // (they now belong to this map)
    src.map.clear();
}

// Iterator implementation
memalloc_heap_map::iterator::iterator()
  : current()
  , end_iter()
{
}

memalloc_heap_map::iterator::iterator(HeapMapType<void*, traceback_t*>::const_iterator it,
                                      HeapMapType<void*, traceback_t*>::const_iterator end)
  : current(it)
  , end_iter(end)
{
}

memalloc_heap_map::iterator&
memalloc_heap_map::iterator::operator++()
{
    if (current != end_iter) {
        ++current;
    }
    return *this;
}

memalloc_heap_map::iterator
memalloc_heap_map::iterator::operator++(int)
{
    iterator tmp = *this;
    ++(*this);
    return tmp;
}

memalloc_heap_map::iterator::value_type
memalloc_heap_map::iterator::operator*() const
{
    if (current == end_iter) {
        return { nullptr, nullptr };
    }
    return { current->first, current->second };
}

bool
memalloc_heap_map::iterator::operator==(const iterator& other) const
{
    return current == other.current;
}

bool
memalloc_heap_map::iterator::operator!=(const iterator& other) const
{
    return current != other.current;
}

memalloc_heap_map::iterator
memalloc_heap_map::begin() const
{
    return iterator(map.begin(), map.end());
}

memalloc_heap_map::iterator
memalloc_heap_map::end() const
{
    return iterator(map.end(), map.end());
}
