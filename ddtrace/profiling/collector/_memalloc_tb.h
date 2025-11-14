#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

#include <Python.h>

struct frame_t
{
    PyObject* filename;
    PyObject* name;
    unsigned int lineno;
} __attribute__((packed));

class traceback_t
{
  public:
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
    /* True if this sample has been reported previously */
    bool reported;
    /* Count of allocations this sample represents (for scaling) */
    size_t count;
    /* List of frames, top frame first */
    std::vector<frame_t> frames;

    /* Constructor - reserves space for the specified number of frames */
    explicit traceback_t(uint16_t nframe);

    /* Destructor - cleans up Python references */
    ~traceback_t();

    // Non-copyable, non-movable
    traceback_t(const traceback_t&) = delete;
    traceback_t& operator=(const traceback_t&) = delete;
    traceback_t(traceback_t&&) = delete;
    traceback_t& operator=(traceback_t&&) = delete;
};

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
memalloc_get_traceback(uint16_t max_nframe, void* ptr, size_t size, PyMemAllocatorDomain domain, size_t weighted_size);

PyObject*
traceback_to_tuple(traceback_t* tb);

/* The maximum number of traceback samples we can store in the heap profiler */
#define TRACEBACK_ARRAY_MAX_COUNT UINT16_MAX
