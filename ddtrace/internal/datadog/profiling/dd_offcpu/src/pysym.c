#include "pysym.h"

#include <stdlib.h>

/*
 * Spike scaffold. The full implementation opens /proc/<pid>/mem, resolves
 * _PyRuntime via the symbolizer, and walks the interpreter/thread/frame chain
 * using a version-keyed offset table. For now the lifecycle is in place and the
 * walk is a stub returning no frames so the daemon links and runs end to end.
 */

struct pysym
{
    pid_t pid;
    int py_major;
    int py_minor;
    /* TODO: mem fd, _PyRuntime address, version-keyed offset table. */
};

struct pysym*
pysym_new(pid_t pid)
{
    struct pysym* py = calloc(1, sizeof(*py));
    if (py == NULL)
        return NULL;
    py->pid = pid;
    /* TODO: detect interpreter version from /proc/<pid>/cmdline or
     * the python ELF .rodata and reject unsupported versions. */
    return py;
}

void
pysym_free(struct pysym* py)
{
    free(py);
}

int
pysym_walk(struct pysym* py, pid_t tid, char (*frames)[256], int max_frames)
{
    (void)py;
    (void)tid;
    (void)frames;
    (void)max_frames;
    /* TODO: walk tstate -> frame chain -> co_qualname/co_name. */
    return 0;
}
