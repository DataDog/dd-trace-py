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

/* Native-heap ownership partition (Phase 2 producer-side de-dup).
 *
 * When enabled, the in-process heap sampler skips OBJ/MEM allocations whose
 * request size exceeds pymalloc's small-request threshold, because pymalloc
 * delegates those to the raw allocator (glibc malloc) where the native-heap
 * gotter already samples them. This avoids double-counting the large-object
 * managed tail across the two producers. Off by default (no behavior change);
 * turned on from Python only when the gotter is actually armed. */
void
memalloc_heap_set_native_heap_partition(bool enabled);

void
memalloc_heap_postfork_child(void);
