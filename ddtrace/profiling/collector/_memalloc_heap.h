#ifndef _DDTRACE_MEMALLOC_HEAP_H
#define _DDTRACE_MEMALLOC_HEAP_H

#include <stddef.h>
#include <stdint.h>

/* The maximum heap sample size is the maximum value we can store in a heap_tracker_t.allocated_memory */
#define MAX_HEAP_SAMPLE_SIZE UINT32_MAX

void
memalloc_heap_tracker_init(void);
void
memalloc_heap_tracker_deinit(void);

void
memalloc_heap_track(uint32_t heap_sample_size, uint16_t max_nframe, void* ptr, size_t size);
void
memalloc_heap_untrack(void* ptr);

#endif
