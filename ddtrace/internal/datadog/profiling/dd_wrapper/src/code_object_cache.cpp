#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "code_object_cache.hpp"

namespace {

// Returns true if the weak reference's referent is still alive
//   < 3.13: PyWeakref_GetObject() is the stable API (borrowed ref)
//  >= 3.13: PyWeakref_GetObject() and PyWeakref_GET_OBJECT() are deprecated so we
//           use PyWeakref_GetRef() (intro in 3.13, returns strong ref through out-param)
inline bool
weakref_is_alive(PyObject* wr)
{
#if PY_VERSION_HEX >= 0x030d0000 // Python 3.13+
    PyObject* obj = nullptr;
    int rc = PyWeakref_GetRef(wr, &obj);
    if (rc == 1) {
        // Referent is alive; obj is a new strong reference so release it immediately
        Py_DECREF(obj);
        return true;
    }
    // rc == 0: referent is dead (obj is NULL)
    // rc == -1: not a weakref (should not happen here); treat as dead
    return false;
#else
    // Python < 3.13: PyWeakref_GetObject returns a borrowed reference
    return PyWeakref_GetObject(wr) != Py_None;
#endif
}

}

namespace Datadog {

CodeObjectFunctionCache&
CodeObjectFunctionCache::instance()
{
    static CodeObjectFunctionCache inst;
    return inst;
}

std::optional<function_id>
CodeObjectFunctionCache::get(PyObject* code)
{
    const auto key = reinterpret_cast<uintptr_t>(code);
    std::lock_guard<std::mutex> lock(mtx_);

    auto it = map_.find(key);
    if (it == map_.end()) {
        return std::nullopt;
    }

    if (!weakref_is_alive(it->second.weak_ref)) {
        // Stale entry: code object was freed. Evict since its a cache miss
        Py_DECREF(it->second.weak_ref);
        map_.erase(it);
        return std::nullopt;
    }

    return it->second.fn_id;
}

void
CodeObjectFunctionCache::insert(PyObject* code, function_id fn_id)
{
    // Create a weak reference with no callback
    PyObject* wr = PyWeakref_NewRef(code, nullptr);
    if (wr == nullptr) {
        // Should not happen for PyCodeObject
        PyErr_Clear();
        return;
    }

    const auto key = reinterpret_cast<uintptr_t>(code);
    std::lock_guard<std::mutex> lock(mtx_);

    // emplace returns {iterator, inserted}.  If two threads race on a cache miss
    // for the same key, the second call loses and we discard the extra weakref.
    // TODO: Take another look at this
    auto [it, inserted] = map_.emplace(key, Entry{ wr, fn_id });
    if (!inserted) {
        Py_DECREF(wr);
    }
}

void
CodeObjectFunctionCache::clear()
{
    std::lock_guard<std::mutex> lock(mtx_);
    // Only call Python's reference counting if the interpreter is still alive.
    // At C++ static-destructor / atexit time the interpreter may already be gone.
    if (Py_IsInitialized()) {
        for (auto& [key, entry] : map_) {
            Py_DECREF(entry.weak_ref);
        }
    }
    map_.clear();
}

void
CodeObjectFunctionCache::postfork_child()
{
    // After fork() the child inherits the parent's heap and all cached
    // function_ids are stale (the ProfilesDictionary is about to be recreated).
    // This mirrors the pattern used in NativeCallRegistry::postfork_child().
    new (&mtx_) std::mutex();

    // Abandon all cache entries without calling Py_DECREF on the weakrefs.
    // Calling Py_DECREF here is unsafe: Python's fork reinitialisation
    // (pthread_atfork child handler) may still be running
    std::lock_guard<std::mutex> lock(mtx_);
    map_.clear();
}

}
