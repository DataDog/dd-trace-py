#ifndef _DDTRACE_MEMALLOC_HEAP_H
#define _DDTRACE_MEMALLOC_HEAP_H

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

#include <Python.h>

#include "_utils.h"

/* The maximum heap sample size is the maximum value we can store in a heap_tracker_t.allocated_memory */
#define MAX_HEAP_SAMPLE_SIZE UINT32_MAX

void
memalloc_heap_tracker_init(uint32_t sample_size);
void
memalloc_heap_tracker_deinit(void);

PyObject*
memalloc_heap();

bool
memalloc_heap_track(uint16_t max_nframe, void* ptr, size_t size);
void
memalloc_heap_untrack(void* ptr);

#define MEMALLOC_HEAP_PTR_ARRAY_MAX UINT64_MAX
DO_ARRAY(void *, ptr, uint64_t, DO_NOTHING)

#endif
