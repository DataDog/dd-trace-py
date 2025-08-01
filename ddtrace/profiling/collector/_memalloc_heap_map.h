#include <stdbool.h>

#include <Python.h>

#include "_memalloc_tb.h"

/* memalloc_heap_map_t tracks sampled allocations by their address.
 * The implementation is opaque from the C perspective;
 * we use a C++ unordered_map internally.
 * C code only works with pointers to this map.
 */
typedef struct memalloc_heap_map_t memalloc_heap_map_t;

typedef struct memalloc_heap_map_iter_t memalloc_heap_map_iter_t;

/* Construct an empty map */
memalloc_heap_map_t*
memalloc_heap_map_new();

size_t
memalloc_heap_map_size(memalloc_heap_map_t* m);

/* Insert a traceback for a sampled allocation with the given address.
 * If there is already an entry for the given key, the old value will be
 * replaced with the given value, and the old value will be returned */
traceback_t*
memalloc_heap_map_insert(memalloc_heap_map_t* m, void* key, traceback_t* value);

bool
memalloc_heap_map_contains(memalloc_heap_map_t* m, void* key);

/* Retrieve the sampled allocation with the given address from m.
 * Returns NULL if the allocation wasn't found */
traceback_t*
memalloc_heap_map_remove(memalloc_heap_map_t* m, void* key);

PyObject*
memalloc_heap_map_export(memalloc_heap_map_t* m);

/* Create a new iterator for the heap map */
memalloc_heap_map_iter_t*
memalloc_heap_map_iter_new(memalloc_heap_map_t* m);

/* Get the next key-value pair from the iterator. Returns true if a pair was found,
 * false if the iterator is exhausted */
bool
memalloc_heap_map_iter_next(memalloc_heap_map_iter_t* it, void** key, traceback_t** tb);

/* Delete the iterator */
void
memalloc_heap_map_iter_delete(memalloc_heap_map_iter_t* it);

/* Copy the contents of src into dst, removing the items from src */
void
memalloc_heap_map_destructive_copy(memalloc_heap_map_t* dst, memalloc_heap_map_t* src);

/* Free memory associated with m */
void
memalloc_heap_map_delete(memalloc_heap_map_t* m);
