#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include <Python.h>

/* The maximum heap sample size is the maximum value we can store in a heap_tracker_t.allocated_memory */
#define MAX_HEAP_SAMPLE_SIZE UINT32_MAX

[[nodiscard]] bool
memalloc_heap_tracker_init_no_cpython(uint32_t sample_size);
void
memalloc_heap_tracker_deinit_no_cpython(void);

void
memalloc_heap_no_cpython(void);

void
memalloc_heap_track_invokes_cpython(uint16_t max_nframe, void* ptr, size_t size, PyMemAllocatorDomain domain);
void
memalloc_heap_untrack_no_cpython(void* ptr);
