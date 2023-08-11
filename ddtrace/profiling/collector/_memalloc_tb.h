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

DO_ARRAY(traceback_t*, traceback, TRACEBACK_ARRAY_COUNT_TYPE, traceback_free)

#endif
