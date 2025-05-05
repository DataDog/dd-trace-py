#include <stdlib.h>

#include <Python.h>

#include "_memalloc_tb.h"
#include "vendor/cwisstable.h"

/* cwisstable.h provides a C implementation of SwissTables hash maps, originally
 * implemented in the Abseil C++ library.
 *
 * This header is was generated from https://github.com/google/cwisstable
 * at commit 6de0e5f2e55f90017534a3366198ce7d3e3b7fef
 * and lightly modified to compile for Windows and 32-bit platforms we support.
 * See "BEGIN MODIFICATION" and "END MODIFICATION" in the header.
 *
 * The following macro will expand to a type-safe implementation with void* keys
 * and traceback_t* values for use by the heap profiler. We encapsulate this
 * implementation in a wrapper specialized for use by the heap profiler, both to
 * keep compilation fast (the cwisstables header is big) and to allow us to swap
 * out the implementation if we want.
 */
#if defined(_WIN_64) || defined(__x86_64__) || defined(__aarch_64__)
CWISS_DECLARE_FLAT_HASHMAP(HeapSamples, void*, traceback_t*);
#else
/* The default cwisstable hash relies on full-width 64-bit
 * multiplication, which is really slow on 32-bit.
 * For 32-bit, we define a custom hash function with reasonable quality.
 * Derived from:
 * https://github.com/Cyan4973/xxHash/blob/dev/doc/xxhash_spec.md#xxh32-algorithm-description
 */
static size_t
void_ptr_hash(const void* value)
{
#define PRIME32_1 0x9E3779B1U
#define PRIME32_2 0x85EBCA77U
#define PRIME32_3 0xC2B2AE3DU
#define PRIME32_4 0x27D4EB2FU
#define PRIME32_5 0x165667B1U

    /* "Special case: input is less than 16 bytes".
     * Here our seed is fixed at 0 so we elide it */
    uint32_t acc = PRIME32_5;

    /* "Input length" is the size of a pointer. */
    acc = acc + sizeof(void*);

    /* "Consume remaining input".
     * Here we know our input is just 4 bytes, the size of a pointer */
    uint32_t lane = *((uint32_t*)value);
    acc += lane * PRIME32_3;
    acc = (acc << 17) * PRIME32_4;

    acc ^= (acc >> 15);
    acc *= PRIME32_2;
    acc ^= (acc >> 13);
    acc *= PRIME32_3;
    acc ^= (acc >> 16);
    return acc;
}
CWISS_DECLARE_FLAT_MAP_POLICY(HeapSamples_policy32, void*, traceback_t*, (key_hash, void_ptr_hash));
CWISS_DECLARE_HASHMAP_WITH(HeapSamples, void*, traceback_t*, HeapSamples_policy32);
#endif

typedef struct memalloc_heap_map_t
{
    HeapSamples map;
} memalloc_heap_map_t;

memalloc_heap_map_t*
memalloc_heap_map_new()
{
    memalloc_heap_map_t* m = calloc(sizeof(memalloc_heap_map_t), 1);
    m->map = HeapSamples_new(0);
    return m;
}

void
memalloc_heap_map_insert(memalloc_heap_map_t* m, void* key, traceback_t* value)
{
    HeapSamples_Entry k = { key = key, value = value };
    HeapSamples_insert(&m->map, &k);
}

bool
memalloc_heap_map_contains(memalloc_heap_map_t* m, void* key)
{
    return HeapSamples_contains(&m->map, &key);
}

traceback_t*
memalloc_heap_map_remove(memalloc_heap_map_t* m, void* key)
{
    /* TODO: erase might not actually shrink the map? We should probably do that
     * periodically? */
    traceback_t* res = NULL;
    HeapSamples_Iter it = HeapSamples_find(&m->map, &key);
    HeapSamples_Entry* e = HeapSamples_Iter_get(&it);
    if (e != NULL) {
        res = e->val;
        HeapSamples_erase_at(it);
    }
    return res;
}

PyObject*
memalloc_heap_map_export(memalloc_heap_map_t* m)
{
    PyObject* heap_list = PyList_New(HeapSamples_size(&m->map));
    if (heap_list == NULL) {
        return NULL;
    }

    int i = 0;
    HeapSamples_CIter it = HeapSamples_citer(&m->map);
    for (const HeapSamples_Entry* e = HeapSamples_CIter_get(&it); e != NULL; e = HeapSamples_CIter_next(&it)) {
        traceback_t* tb = e->val;

        PyObject* tb_and_size = PyTuple_New(2);
        PyTuple_SET_ITEM(tb_and_size, 0, traceback_to_tuple(tb));
        PyTuple_SET_ITEM(tb_and_size, 1, PyLong_FromSize_t(tb->size));
        PyList_SET_ITEM(heap_list, i, tb_and_size);
        i++;
    }
    return heap_list;
}

void
memalloc_heap_map_destructive_copy(memalloc_heap_map_t* dst, memalloc_heap_map_t* src)
{
    /* TODO: is this any better than copy followed by clear? _Maybe_ this
     * saves memory, at the expense of doing more work to remove stuff as we
     * go?
     */
    HeapSamples_Iter it = HeapSamples_iter(&src->map);
    for (const HeapSamples_Entry* e = HeapSamples_Iter_get(&it); e != NULL; e = HeapSamples_Iter_next(&it)) {
        HeapSamples_insert(&dst->map, e);
        /* TODO: erase might not actually shrink the map?  I think we need to clear
         * it at the end of this function if we want to reclaim the memory. */
        HeapSamples_erase_at(it);
    }
}

void
memalloc_heap_map_delete(memalloc_heap_map_t* m)
{
    HeapSamples_CIter it = HeapSamples_citer(&m->map);
    for (const HeapSamples_Entry* e = HeapSamples_CIter_get(&it); e != NULL; e = HeapSamples_CIter_next(&it)) {
        traceback_free(e->val);
    }
    HeapSamples_destroy(&m->map);
    free(m);
}
