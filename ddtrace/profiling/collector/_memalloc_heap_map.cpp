#include <unordered_map>

#include <Python.h>

/* We can't include "_memalloc_tb.h" because C++ doesn't like the preprocessor stuff */
typedef struct taceback_t traceback_t;
extern "C" void
traceback_free(traceback_t* tb);
extern "C" PyObject*
traceback_to_tuple(traceback_t* tb);
extern "C" size_t
traceback_alloc_size(traceback_t* tb);

struct VoidPointerHash
{
    size_t operator()(const void* ptr) const
    {
        /* The default performance of the map with void * keys is really bad.
         * I think the default hashing is not good. Not that it is slow to compute, but
         * rather that it leads to bad access patterns in the map.
         * The keys are allocated memory address which likely agree in the low bits.
         * Shift off the low bits to get slightly better entropy.
         */
        return std::hash<uintptr_t>{}(reinterpret_cast<uintptr_t>(ptr) >> 3);
    }
};

struct VoidPointerEqual
{
    bool operator()(const void* a, const void* b) const { return a == b; }
};

typedef struct memalloc_heap_map_t
{
    std::unordered_map<void*, traceback_t*, VoidPointerHash, VoidPointerEqual> m;
} memalloc_heap_map_t;

extern "C"
{
    memalloc_heap_map_t* memalloc_heap_map_new()
    {
        memalloc_heap_map_t* m = new memalloc_heap_map_t;
        return m;
    }

    void memalloc_heap_map_insert(memalloc_heap_map_t* m, void* key, traceback_t* value)
    {
        m->m[key] = value;
    }

    bool memalloc_heap_map_contains(memalloc_heap_map_t* m, void* key)
    {
        return m->m.find(key) != m->m.end();
    }

    traceback_t* memalloc_heap_map_remove(memalloc_heap_map_t* m, void* key)
    {
        traceback_t* res = nullptr;
        auto it = m->m.find(key);
        if (it != m->m.end()) {
            res = it->second;
            m->m.erase(it);
        }
        return res;
    }

    PyObject* memalloc_heap_map_export(memalloc_heap_map_t* m)
    {
        PyObject* heap_list = PyList_New(m->m.size());
        if (heap_list == nullptr) {
            return nullptr;
        }

        int i = 0;
        for (auto it : m->m) {
            traceback_t* tb = it.second;

            PyObject* tb_and_size = PyTuple_New(2);
            PyTuple_SET_ITEM(tb_and_size, 0, traceback_to_tuple(tb));
            PyTuple_SET_ITEM(tb_and_size, 1, PyLong_FromSize_t(traceback_alloc_size(tb)));
            PyList_SET_ITEM(heap_list, i, tb_and_size);
            i++;
        }
        return heap_list;
    }

    void memalloc_heap_map_destructive_copy(memalloc_heap_map_t* dst, memalloc_heap_map_t* src)
    {
        /* TODO: is this any better than copy followed by clear? _Maybe_ this
         * saves memory, at the expense of doing more work to remove stuff as we
         * go?
         */
        for (auto it = src->m.begin(); it != src->m.end();) {
            dst->m.insert(*it);
            it = src->m.erase(it);
        }
    }

    void memalloc_heap_map_delete(memalloc_heap_map_t* m)
    {
        for (auto it : m->m) {
            traceback_free(it.second);
        }
        delete m;
    }
}
