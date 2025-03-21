#ifndef _DDTRACE_MEMALLOC_HEAP_H
#define _DDTRACE_MEMALLOC_HEAP_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include <Python.h>

#include "_memalloc.h"
#include "_utils.h"

/* The maximum heap sample size is the maximum value we can store in a heap_tracker_t.allocated_memory */
#define MAX_HEAP_SAMPLE_SIZE UINT32_MAX

void
memalloc_heap_tracker_init(memalloc_context_t* ctx, uint32_t sample_size);
void
memalloc_heap_tracker_deinit(memalloc_context_t* ctx);

PyObject*
memalloc_heap(memalloc_context_t* ctx);

bool
memalloc_heap_track(memalloc_context_t* ctx, uint16_t max_nframe, void* ptr, size_t size, PyMemAllocatorDomain domain);
void
memalloc_heap_untrack(memalloc_context_t* ctx, void* ptr);

#endif
