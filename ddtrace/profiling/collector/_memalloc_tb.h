#ifndef _DDTRACE_MEMALLOC_TB_H
#define _DDTRACE_MEMALLOC_TB_H

#include <stdint.h>

#include <Python.h>

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
    /* Thread ID */
    unsigned long thread_id;
    /* List of frames, top frame first */
    frame_t frames[1];
} traceback_t;

/* The maximum number of frames we can store in `traceback_t.nframe` */
#define TRACEBACK_MAX_NFRAME UINT16_MAX

/* List of tracebacks */
typedef struct
{
    /* List of traceback */
    traceback_t** tracebacks;
    /* Number of traceback in the list of traceback */
    uint16_t count;
    /* Total number of allocations */
    uint64_t alloc_count;
} traceback_list_t;

/* The maximum number of events we can store in `traceback_list_t.count` */
#define TRACEBACK_LIST_MAX_COUNT UINT16_MAX

int
memalloc_tb_init(uint16_t max_nframe);
void
memalloc_tb_deinit();

void
traceback_free(traceback_t* tb);

traceback_t*
memalloc_get_traceback(uint16_t max_nframe, size_t size);

#endif
