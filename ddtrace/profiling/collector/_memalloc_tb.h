#ifndef _DDTRACE_MEMALLOC_TB_H
#define _DDTRACE_MEMALLOC_TB_H

#include <stdbool.h>
#include <stdint.h>

#include <Python.h>

#include "_utils.h"

typedef struct
#ifdef __GNUC__
  __attribute__((packed))
#elif defined(_MSC_VER)
#pragma pack(push, 4)
#endif
{
    PyObject* filename;
    PyObject* name;
    unsigned int lineno;
} frame_t;
#if defined(_MSC_VER)
#pragma pack(pop)
#endif

typedef struct
{
    /* Total number of frames in the traceback */
    uint16_t total_nframe;
    /* Number of frames in the traceback */
    uint16_t nframe;
    /* Memory pointer allocated */
    void* ptr;
    /* Memory size allocated in bytes */
    size_t size;
    /* Domain allocated */
    PyMemAllocatorDomain domain;
    /* Thread ID */
    unsigned long thread_id;
    /* List of frames, top frame first */
    frame_t frames[1];
} traceback_t;

/* The maximum number of frames we can store in `traceback_t.nframe` */
#define TRACEBACK_MAX_NFRAME UINT16_MAX

bool
memalloc_ddframe_class_init();

int
memalloc_tb_init(uint16_t max_nframe);
void
memalloc_tb_deinit();

void
traceback_free(traceback_t* tb);

traceback_t*
memalloc_get_traceback(uint16_t max_nframe, void* ptr, size_t size, PyMemAllocatorDomain domain);

PyObject*
traceback_to_tuple(traceback_t* tb);

/* The maximum number of events we can store in `traceback_array_t.count` */
#define TRACEBACK_ARRAY_MAX_COUNT UINT16_MAX
#define TRACEBACK_ARRAY_COUNT_TYPE uint16_t

//DO_ARRAY(traceback_t*, traceback, TRACEBACK_ARRAY_COUNT_TYPE, traceback_free)


typedef struct traceback_array_t { traceback_t** tab; uint16_t count, size; } traceback_array_t;

static inline void
traceback_array_init(traceback_array_t* arr)
{
  arr->count = 0;
  arr->size = 0;
  arr->tab = ((void *)0);
}

static inline traceback_array_t*
traceback_array_new(void) {
  traceback_array_t* a = PyMem_RawMalloc(sizeof(traceback_array_t) * (1));
  traceback_array_init(a);
  return a;
}

static inline void
traceback_array_wipe(traceback_array_t* arr)
{
  for (uint16_t i = 0; i < arr->count; i++) {
    traceback_free(arr->tab[i]);
  }
  PyMem_Free(arr->tab);
}

static inline void
traceback_array_delete(traceback_array_t* arrp)
{
  traceback_array_wipe(arrp);
  PyMem_Free(arrp);
}

static inline void
traceback_array_grow(traceback_array_t* arr, uint16_t newlen)
{
  do {
    if ((newlen) > *(&arr->size)) {
      if ((((*(&arr->size)) + 16) * 3 / 2) < (newlen)) {
        *(&arr->size) = (newlen);
      } else {
        *(&arr->size) = (((*(&arr->size)) + 16) * 3 / 2);
      }

      do {
        (arr->tab) = PyMem_RawRealloc((arr->tab), sizeof(*arr->tab) * (*(&arr->size)));
      } while (0);
    }
  } while (0);
}

static inline void
traceback_array_splice( traceback_array_t* arr, uint16_t pos, uint16_t len, traceback_t* items[], uint16_t count)
{
  assert(pos <= arr->count && pos + len <= arr->count);

  if (len != count) {
    traceback_array_grow(arr, arr->count + count - len);
    if (!arr->tab) {
        fprintf(stderr, "Failed to regrow.\n");
        fflush(stderr);
        return;
    }
    memmove(arr->tab + pos + count, arr->tab + pos + len, (arr->count - pos - len) * sizeof(*items));
    arr->count += count - len;
  }
  if (count) {
    if (arr == NULL || items == NULL) {
        fprintf(stderr, "Null pointer argument.\n");
        fflush(stderr);
        return;
    }
    if (pos > arr->count || pos + len > arr->count) {
        fprintf(stderr, "Position or length exceeds array bounds.\n");
        fflush(stderr);
        return;
    }
    for (int i = 0; i < count * sizeof(*items); ++i) {
      volatile char x = ((char*)(arr->tab + pos))[i];
      volatile char y = ((char*)(items))[i];
      (void)x;
      (void)y;
    }
    memmove(arr->tab + pos, items, count * sizeof(*items));
  }
}

static inline traceback_t*
traceback_array_take(traceback_array_t* arr, uint16_t pos)
{
  traceback_t* res = arr->tab[pos]; traceback_array_splice(arr, pos, 1, ((void *)0) , 0);
  return res;
}

static inline uint16_t
traceback_array_indexof(traceback_array_t* arr, traceback_t** e) {
  return e - arr->tab;
}

static inline traceback_t*
traceback_array_remove(traceback_array_t* arr, traceback_t** e) {
  return traceback_array_take(arr, traceback_array_indexof(arr, e));
}

static inline void
traceback_array_push(traceback_array_t* arr, traceback_t* e) {
  traceback_array_splice(arr, 0, 0, &e, 1);
}

static inline void
traceback_array_append(traceback_array_t* arr, traceback_t* e) {
  traceback_array_splice(arr, arr->count, 0, &e, 1);
}

#endif
