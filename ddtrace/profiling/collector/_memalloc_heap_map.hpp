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
    memalloc_heap_map();
    ~memalloc_heap_map();

    // Delete copy constructor and assignment operator
    memalloc_heap_map(const memalloc_heap_map&) = delete;
    memalloc_heap_map& operator=(const memalloc_heap_map&) = delete;

    size_t size() const;

    /* Insert a traceback for a sampled allocation with the given address.
     * If there is already an entry for the given key, the old value will be
     * replaced with the given value, and the old value will be returned */
    traceback_t* insert(void* key, traceback_t* value);

    bool contains(void* key) const;

    /* Retrieve the sampled allocation with the given address from m.
     * Returns nullptr if the allocation wasn't found */
    traceback_t* remove(void* key);

    PyObject* export_to_python() const;

    /* Copy the contents of src into this map, removing the items from src */
    void destructive_copy_from(memalloc_heap_map& src);

    class iterator
    {
      public:
        // Iterator traits
        using iterator_category = std::forward_iterator_tag;
        using value_type = std::pair<void*, traceback_t*>;
        using difference_type = std::ptrdiff_t;
        using pointer = value_type*;
        using reference = value_type&;

        iterator();
        iterator(HeapMapType<void*, traceback_t*>::const_iterator it,
                 HeapMapType<void*, traceback_t*>::const_iterator end);
        ~iterator() = default;

        // Iterator operations
        iterator& operator++();
        iterator operator++(int);
        value_type operator*() const;
        bool operator==(const iterator& other) const;
        bool operator!=(const iterator& other) const;

      private:
        HeapMapType<void*, traceback_t*>::const_iterator current;
        HeapMapType<void*, traceback_t*>::const_iterator end_iter;
        friend class memalloc_heap_map;
    };

    iterator begin() const;
    iterator end() const;

  private:
    HeapMapType<void*, traceback_t*> map;
};
