#include <stddef.h>

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
        iterator(const memalloc_heap_map& map);
        ~iterator();
        bool next(void** key, traceback_t** tb);

      private:
        class Impl;
        Impl* impl;
        friend class memalloc_heap_map;
    };

  private:
    class Impl;
    Impl* impl;
};

// C compatibility typedef (for existing code)
typedef memalloc_heap_map memalloc_heap_map_t;
typedef memalloc_heap_map::iterator memalloc_heap_map_iter_t;
