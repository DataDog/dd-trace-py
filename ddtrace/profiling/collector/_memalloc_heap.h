#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include <Python.h>

/* The maximum heap sample size is the maximum value we can store in a heap_tracker_t.allocated_memory */
#define MAX_HEAP_SAMPLE_SIZE UINT32_MAX

/* --- Prepended header for sampled allocations ---
 *
 * Sampled allocations have a 16-byte header prepended:
 *   [SIGNATURE (8 bytes)] [metadata_ptr (8 bytes)] [user data ...]
 *                                                   ^-- returned to Python
 *
 * On free(), we read 16 bytes before the pointer.  If the signature matches,
 * we know the allocation was sampled and can extract the metadata pointer
 * directly — no hashmap lookup required.
 */

/* Magic value written at the start of every sampled-allocation header.
 * Chosen to be unlikely to appear in normal heap data. */
#define MEMALLOC_SIGNATURE UINT64_C(0xDD74ACE0DEAD0001)

/* Total size of the prepended header (signature + metadata pointer). */
#define MEMALLOC_HEADER_SIZE (sizeof(uint64_t) + sizeof(void*))

/* The header layout prepended to sampled allocations. */
typedef struct
{
    uint64_t signature; /* Must equal MEMALLOC_SIGNATURE */
    void* metadata_ptr; /* Points to the traceback_t that owns this allocation */
} memalloc_header_t;

[[nodiscard]] bool
memalloc_heap_tracker_init_no_cpython(uint32_t sample_size);
void
memalloc_heap_tracker_deinit_no_cpython(void);

void
memalloc_heap_no_cpython(void);

/* Check whether we should sample an allocation of the given size.
 * Must be called with the GIL held. Returns true if we should sample,
 * and sets allocated_memory_val to the current allocated_memory value
 * (used as the weight for the sample). */
bool
memalloc_heap_should_sample_no_cpython(size_t size, uint64_t* allocated_memory_val);

/* Collect a traceback and track the sampled allocation. The header at
 * user_ptr - MEMALLOC_HEADER_SIZE will be written with the signature
 * and metadata pointer. Must be called after should_sample returns true.
 * allocated_memory_val is the weight from should_sample. */
void
memalloc_heap_track_invokes_cpython(uint16_t max_nframe,
                                    void* user_ptr,
                                    size_t size,
                                    uint64_t allocated_memory_val,
                                    PyMemAllocatorDomain domain);

/* Check whether user_ptr was a sampled allocation by reading the prepended
 * header.  Returns true if the signature matches, false otherwise.
 * Note: this only checks the signature, not the metadata pointer. A sampled
 * allocation may have a null metadata pointer if tracking failed/was cleared. */
bool
memalloc_heap_is_sampled(void* user_ptr);

/* Untrack a sampled allocation given the traceback_t* extracted from its header.
 * Removes the traceback from the intrusive list and returns it to the pool. */
void
memalloc_heap_untrack_from_header_no_cpython(void* metadata_ptr);

void
memalloc_heap_postfork_child(void);
