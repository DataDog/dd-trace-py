#pragma once

#include <cstddef>
#include <iterator>
#include <utility>

#include "_memalloc_tb.h"
#include <Python.h>

/* Use Abseil's flat_hash_map for tracking sampled allocations.
 * flat_hash_map provides excellent performance with low memory overhead,
 * using the Swiss Tables algorithm from Abseil.
 *
 * We use a conditional compilation to fall back to std::unordered_map
 * when Abseil is not available (e.g., in Debug builds).
 */
#if defined(NDEBUG) && !defined(DONT_COMPILE_ABSEIL)
#include "absl/container/flat_hash_map.h"
template<typename K, typename V>
using HeapMapType = absl::flat_hash_map<K, V>;
#else
#include <unordered_map>
template<typename K, typename V>
using HeapMapType = std::unordered_map<K, V>;
#endif

/* memalloc_heap_map tracks sampled allocations by their address.
 * C++ interface - implementation is in _memalloc_heap_map.cpp
 */
class memalloc_heap_map
{
  public:
    using map_type = HeapMapType<void*, traceback_t*>;
    using iterator = map_type::const_iterator;
    using const_iterator = map_type::const_iterator;

    memalloc_heap_map() = default;
    ~memalloc_heap_map();

    // Delete copy constructor and assignment operator
    memalloc_heap_map(const memalloc_heap_map&) = delete;
    memalloc_heap_map& operator=(const memalloc_heap_map&) = delete;

    /* Size of the map */
    size_t size() const { return map.size(); }

    /* Check if key exists in the map */
    bool contains(void* key) const { return map.find(key) != map.end(); }

    /* Insert a traceback for a sampled allocation with the given address.
     * If there is already an entry for the given key, the old value will be
     * replaced with the given value and deleted. */
    void insert(void* key, traceback_t* value);

    /* Retrieve the sampled allocation with the given address from the map.
     * Returns nullptr if the allocation wasn't found */
    traceback_t* remove(void* key);

    /* Copy the contents of src into this map, removing the items from src */
    void destructive_copy_from(memalloc_heap_map& src);

    /* Iterators for range-based for loops */
    const_iterator begin() const { return map.begin(); }
    const_iterator end() const { return map.end(); }

  private:
    map_type map;
};
