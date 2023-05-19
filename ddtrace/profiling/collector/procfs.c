#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <fcntl.h>
#include <pthread.h>
#include <sched.h>

#define PY_SSIZE_T_CLEAN
#include <Python.h>

// ---- Hash set --------------------------------------------------------------

struct hashset_st
{
    size_t nbits;
    size_t mask;

    size_t capacity;
    size_t *items;
    size_t nitems;
    size_t n_deleted_items;
};

typedef struct hashset_st *hashset_t;

/* create hashset instance */
hashset_t hashset_create(void);

/* destroy hashset instance */
void hashset_destroy(hashset_t set);

size_t hashset_num_items(hashset_t set);

/* add item into the hashset.
 *
 * @note 0 and 1 is special values, meaning nil and deleted items. the
 *       function will return -1 indicating error.
 *
 * returns zero if the item already in the set and non-zero otherwise
 */
int hashset_add(hashset_t set, void *item);

/* remove item from the hashset
 *
 * returns non-zero if the item was removed and zero if the item wasn't
 * exist
 */
int hashset_remove(hashset_t set, void *item);

/* check if existence of the item
 *
 * returns non-zero if the item exists and zero otherwise
 */
int hashset_is_member(hashset_t set, void *item);

static const unsigned int prime_1 = 73;
static const unsigned int prime_2 = 5009;

hashset_t hashset_create()
{
    hashset_t set = calloc(1, sizeof(struct hashset_st));

    if (set == NULL)
    {
        return NULL;
    }
    set->nbits = 3;
    set->capacity = (size_t)(1 << set->nbits);
    set->mask = set->capacity - 1;
    set->items = calloc(set->capacity, sizeof(size_t));
    if (set->items == NULL)
    {
        hashset_destroy(set);
        return NULL;
    }
    set->nitems = 0;
    set->n_deleted_items = 0;
    return set;
}

size_t hashset_num_items(hashset_t set)
{
    return set->nitems;
}

void hashset_destroy(hashset_t set)
{
    if (set)
    {
        free(set->items);
    }
    free(set);
}

static int hashset_add_member(hashset_t set, void *item)
{
    size_t value = (size_t)item;
    size_t ii;

    if (value == 0 || value == 1)
    {
        return -1;
    }

    ii = set->mask & (prime_1 * value);

    while (set->items[ii] != 0 && set->items[ii] != 1)
    {
        if (set->items[ii] == value)
        {
            return 0;
        }
        else
        {
            /* search free slot */
            ii = set->mask & (ii + prime_2);
        }
    }
    set->nitems++;
    if (set->items[ii] == 1)
    {
        set->n_deleted_items--;
    }
    set->items[ii] = value;
    return 1;
}

static void maybe_rehash(hashset_t set)
{
    size_t *old_items;
    size_t old_capacity, ii;

    if (set->nitems + set->n_deleted_items >= (double)set->capacity * 0.85)
    {
        old_items = set->items;
        old_capacity = set->capacity;
        set->nbits++;
        set->capacity = (size_t)(1 << set->nbits);
        set->mask = set->capacity - 1;
        set->items = calloc(set->capacity, sizeof(size_t));
        set->nitems = 0;
        set->n_deleted_items = 0;
        assert(set->items);
        for (ii = 0; ii < old_capacity; ii++)
        {
            hashset_add_member(set, (void *)old_items[ii]);
        }
        free(old_items);
    }
}

int hashset_add(hashset_t set, void *item)
{
    int rv = hashset_add_member(set, item);
    maybe_rehash(set);
    return rv;
}

int hashset_remove(hashset_t set, void *item)
{
    size_t value = (size_t)item;
    size_t ii = set->mask & (prime_1 * value);

    while (set->items[ii] != 0)
    {
        if (set->items[ii] == value)
        {
            set->items[ii] = 1;
            set->nitems--;
            set->n_deleted_items++;
            return 1;
        }
        else
        {
            ii = set->mask & (ii + prime_2);
        }
    }
    return 0;
}

int hashset_is_member(hashset_t set, void *item)
{
    size_t value = (size_t)item;
    size_t ii = set->mask & (prime_1 * value);

    while (set->items[ii] != 0)
    {
        if (set->items[ii] == value)
        {
            return 1;
        }
        else
        {
            ii = set->mask & (ii + prime_2);
        }
    }
    return 0;
}

// ----------------------------------------------------------------------------

static hashset_t thread_state_cache = NULL;
static pthread_mutex_t thread_state_cache_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_t thread_state_thread = 0;
static pid_t collector_pid = 0;
static pid_t collector_tid = 0;
static char collector_tid_str[16];

static int
is_pid_dir(const struct dirent *entry)
{
    const char *p;

    for (p = entry->d_name; *p; p++)
    {
        if (!isdigit(*p))
            return 0;
    }

    return 1;
}

static int
thread_is_running(char *tid)
{
    char file_name[128];
    char buffer[2048] = "";

    sprintf(file_name, "/proc/self/task/%s/stat", tid);

    int fd = open(file_name, O_RDONLY);
    if (fd == -1)
    {
        return -1;
    }

    if (read(fd, buffer, 2047) == 0)
    {
        close(fd);
        return -1;
    }

    char *p = strchr(buffer, ')');
    if (p == NULL)
    {
        close(fd);
        return -1;
    }

    close(fd);

    p += 2;
    if (*p == ' ')
        p++;

    return (*p == 'R');
}

static void *
update_thread_state(void *arg)
{
    for (;;)
    {
        if (thread_is_running(collector_tid_str))
        {
            // If the collector thread is running we don't want to update the
            // thread state cache because we know that the collector is running
            // and hence holding the GIL.
            sched_yield();
            continue;
        }

        pthread_mutex_lock(&thread_state_cache_lock);

        if (thread_state_cache != NULL)
            hashset_destroy(thread_state_cache);
        thread_state_cache = hashset_create();

        // Iterate over all the threads in the process
        FILE *fp;
        struct dirent *entry;
        int pid;
        unsigned long maj_faults;
        DIR *dir = opendir("/proc/self/task");
        if (dir == NULL)
        {
            pthread_mutex_unlock(&thread_state_cache_lock);
            return NULL;
        }

        while ((entry = readdir(dir)))
        {
            // Skip anything that is not a PID directory.
            if (!is_pid_dir(entry))
                continue;

            if (thread_is_running(entry->d_name))
            {
                hashset_add(thread_state_cache, (void *)atoi(entry->d_name));
            }
        }

        closedir(dir);

        pthread_mutex_unlock(&thread_state_cache_lock);

        sched_yield();
    }
}

static PyObject *
start(PyObject *Py_UNUSED(module), PyObject *Py_UNUSED(args))
{
    collector_pid = getpid();
    collector_tid = gettid();
    sprintf(collector_tid_str, "%d", collector_tid);

    if (thread_state_thread != 0)
        Py_RETURN_NONE;

    // Create the set that will hold the TIDs of all the running threads within
    // the process.
    if (thread_state_cache != NULL)
    {
        hashset_destroy(thread_state_cache);
    }
    thread_state_cache = hashset_create();

    // Start the updater thread
    thread_state_thread = pthread_create(&thread_state_thread, NULL, update_thread_state, NULL);

    Py_RETURN_NONE;
}

static PyObject *
stop(PyObject *Py_UNUSED(module), PyObject *Py_UNUSED(args))
{
    // Garbage collect the set of running threads
    if (thread_state_cache != NULL)
    {
        hashset_destroy(thread_state_cache);
        thread_state_cache = NULL;
    }

    // Stop the updater thread
    if (thread_state_thread != 0)
    {
        pthread_cancel(thread_state_thread);
        thread_state_thread = 0;
    }

    Py_RETURN_NONE;
}

static PyObject *
thread__is_running(PyObject *Py_UNUSED(module), PyObject *args)
{
    if (thread_state_cache == NULL)
        Py_RETURN_TRUE;

    pid_t tid = 0;

    if (!PyArg_ParseTuple(args, "i", &tid))
        return NULL;

    // If the TID is that of the collector, then obviously it is running, since
    // we expect this function to be called from the collector thread.
    if (tid == collector_tid)
    {
        Py_RETURN_TRUE;
    }

    pthread_mutex_lock(&thread_state_cache_lock);
    int is_running = hashset_is_member(thread_state_cache, (void *)tid);
    pthread_mutex_unlock(&thread_state_cache_lock);

    if (is_running)
    {
        Py_RETURN_TRUE;
    }

    Py_RETURN_FALSE;
}

static PyMethodDef module_methods[] = {
    {"start", (PyCFunction)start, METH_NOARGS, NULL},
    {"stop", (PyCFunction)stop, METH_NOARGS, NULL},
    {"thread_is_running", (PyCFunction)thread__is_running, METH_VARARGS, NULL},
    /* sentinel */
    {NULL, NULL, 0, NULL}};

static struct PyModuleDef module_def = {
    PyModuleDef_HEAD_INIT,
    "procfs",
    NULL,
    0, /* non-negative size to be able to unload the module */
    module_methods,
    NULL,
    NULL,
    NULL,
    NULL,
};

PyMODINIT_FUNC
PyInit_procfs(void)
{
    PyObject *m;
    m = PyModule_Create(&module_def);
    if (m == NULL)
        return NULL;

    return m;
}
