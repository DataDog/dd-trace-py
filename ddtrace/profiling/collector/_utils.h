#ifndef _DDTRACE_UTILS_H
#define _DDTRACE_UTILS_H

#include <Python.h>
#include <stdlib.h>


static inline uint64_t
random_range(uint64_t max)
{
    /* Return a random number between [0, max[ */
    return (uint64_t)((double)rand() / ((double)RAND_MAX + 1) * max);
}


#define DO_NOTHING(...)

#define p_new(type, count) PyMem_RawMalloc(sizeof(type) * (count))
#define p_delete(mem_p) PyMem_Free(mem_p);
// Allocate at least 16 and 50% more than requested to avoid allocating items one by one.
#define p_alloc_nr(x) (((x) + 16) * 3 / 2)
#define p_realloc(p, count)                                                                                            \
    do {                                                                                                               \
        (p) = PyMem_RawRealloc((p), sizeof(*p) * (count));                                                             \
    } while (0)

#define p_grow(p, goalnb, allocnb)                                                                                     \
    do {                                                                                                               \
        if ((goalnb) > *(allocnb)) {                                                                                   \
            if (p_alloc_nr(*(allocnb)) < (goalnb)) {                                                                   \
                *(allocnb) = (goalnb);                                                                                 \
            } else {                                                                                                   \
                *(allocnb) = p_alloc_nr(*(allocnb));                                                                   \
            }                                                                                                          \
            p_realloc(p, *(allocnb));                                                                                  \
        }                                                                                                              \
    } while (0)

/** Common array type */
#define ARRAY_TYPE(type_t, pfx, size_type)                                                                             \
    typedef struct pfx##_array_t                                                                                       \
    {                                                                                                                  \
        type_t* tab;                                                                                                   \
        size_type count, size;                                                                                         \
    } pfx##_array_t;

/** Common array functions */
#define ARRAY_COMMON_FUNCS(type_t, pfx, size_type, dtor)                                                               \
    static inline void pfx##_array_init(pfx##_array_t* arr)                                                            \
    {                                                                                                                  \
        arr->count = 0;                                                                                                \
        arr->size = 0;                                                                                                 \
        arr->tab = NULL;                                                                                               \
    }                                                                                                                  \
    static inline pfx##_array_t* pfx##_array_new(void)                                                                 \
    {                                                                                                                  \
        pfx##_array_t* a = p_new(pfx##_array_t, 1);                                                                    \
        pfx##_array_init(a);                                                                                           \
        return a;                                                                                                      \
    }                                                                                                                  \
    static inline void pfx##_array_wipe(pfx##_array_t* arr)                                                            \
    {                                                                                                                  \
        for (size_type i = 0; i < arr->count; i++) {                                                                   \
            dtor(arr->tab[i]);                                                                                         \
        }                                                                                                              \
        p_delete(arr->tab);                                                                                            \
    }                                                                                                                  \
    static inline void pfx##_array_delete(pfx##_array_t* arrp)                                                         \
    {                                                                                                                  \
        pfx##_array_wipe(arrp);                                                                                        \
        p_delete(arrp);                                                                                                \
    }                                                                                                                  \
                                                                                                                       \
    static inline void pfx##_array_grow(pfx##_array_t* arr, size_type newlen)                                          \
    {                                                                                                                  \
        p_grow(arr->tab, newlen, &arr->size);                                                                          \
    }                                                                                                                  \
    static inline void pfx##_array_splice(                                                                             \
      pfx##_array_t* arr, size_type pos, size_type len, type_t items[], size_type count)                               \
    {                                                                                                                  \
        assert(pos >= 0 && len >= 0 && count >= 0);                                                                    \
        assert(pos <= arr->count && pos + len <= arr->count);                                                          \
        if (len != count) {                                                                                            \
            pfx##_array_grow(arr, arr->count + count - len);                                                           \
            memmove(arr->tab + pos + count, arr->tab + pos + len, (arr->count - pos - len) * sizeof(*items));          \
            arr->count += count - len;                                                                                 \
        }                                                                                                              \
        if (count)                                                                                                     \
            memcpy(arr->tab + pos, items, count * sizeof(*items));                                                     \
    }                                                                                                                  \
    static inline type_t pfx##_array_take(pfx##_array_t* arr, size_type pos)                                           \
    {                                                                                                                  \
        type_t res = arr->tab[pos];                                                                                    \
        pfx##_array_splice(arr, pos, 1, NULL, 0);                                                                      \
        return res;                                                                                                    \
    }                                                                                                                  \
    static inline size_type pfx##_array_indexof(pfx##_array_t* arr, type_t* e) { return e - arr->tab; }                \
    static inline type_t pfx##_array_remove(pfx##_array_t* arr, type_t* e)                                             \
    {                                                                                                                  \
        return pfx##_array_take(arr, pfx##_array_indexof(arr, e));                                                     \
    }

#define ARRAY_FUNCS(type_t, pfx, size_type, dtor)                                                                      \
    ARRAY_COMMON_FUNCS(type_t, pfx, size_type, dtor)                                                                   \
    static inline void pfx##_array_push(pfx##_array_t* arr, type_t e) { pfx##_array_splice(arr, 0, 0, &e, 1); }        \
    static inline void pfx##_array_append(pfx##_array_t* arr, type_t e)                                                \
    {                                                                                                                  \
        pfx##_array_splice(arr, arr->count, 0, &e, 1);                                                                 \
    }

#define DO_ARRAY(type_t, pfx, size_type, dtor)                                                                         \
    ARRAY_TYPE(type_t, pfx, size_type)                                                                                 \
    ARRAY_FUNCS(type_t, pfx, size_type, dtor)

#endif
