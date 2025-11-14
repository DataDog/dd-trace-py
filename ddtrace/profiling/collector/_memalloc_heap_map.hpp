#pragma once

#include <cstddef>
#include <iterator>
#include <utility>

#include "_memalloc_tb.h"
#include <Python.h>

/* cwisstable.h provides a C implementation of SwissTables hash maps, originally
 * implemented in the Abseil C++ library.
 *
 * This header is was generated from https://github.com/google/cwisstable
 * at commit 6de0e5f2e55f90017534a3366198ce7d3e3b7fef
 * and lightly modified for our use.
 * See "BEGIN MODIFICATION" and "END MODIFICATION" in the header.
 *
 * The following macro will expand to a type-safe implementation with void* keys
 * and traceback_t* values for use by the heap profiler. We encapsulate this
 * implementation in a wrapper specialized for use by the heap profiler, both to
 * keep compilation fast (the cwisstables header is big) and to allow us to swap
 * out the implementation if we want.
 */
#include "vendor/cwisstable.h"
CWISS_DECLARE_FLAT_HASHMAP(HeapSamples, void*, traceback_t*);

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
        iterator(const memalloc_heap_map& map);
        ~iterator() = default;

        // Iterator operations
        iterator& operator++();
        iterator operator++(int);
        value_type operator*() const;
        bool operator==(const iterator& other) const;
        bool operator!=(const iterator& other) const;

      private:
        HeapSamples_CIter iter;
        friend class memalloc_heap_map;
    };

    iterator begin() const;
    iterator end() const;

  private:
    HeapSamples map;
};
