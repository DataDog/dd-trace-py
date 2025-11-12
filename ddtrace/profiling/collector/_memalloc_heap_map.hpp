#include <iterator>
#include <stddef.h>
#include <utility>

#include <Python.h>

#include "_memalloc_tb.h"

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
     * Returns NULL if the allocation wasn't found */
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
        ~iterator();

        // Iterator operations
        iterator& operator++();
        iterator operator++(int);
        value_type operator*() const;
        bool operator==(const iterator& other) const;
        bool operator!=(const iterator& other) const;

      private:
        class Impl;
        Impl* impl;
        friend class memalloc_heap_map;
    };

    iterator begin() const;
    iterator end() const;

  private:
    class Impl;
    Impl* impl;
};
