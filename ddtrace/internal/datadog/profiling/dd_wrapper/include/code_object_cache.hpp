#pragma once

#ifndef Py_PYTHON_H
struct _object;                  // NOLINT(bugprone-reserved-identifier) -- CPython ABI type, intentional
typedef struct _object PyObject; // NOLINT(bugprone-reserved-identifier)
#endif

#include "sample.hpp" // for Datadog::function_id

#include <mutex>
#include <optional>
#include <unordered_map>

namespace Datadog {

// CodeObjectFunctionCache maps PyCodeObject* addresses to libdatadog function_id values,
// eliminating repeated FFI calls to intern_string / intern_function on hot paths.
//
// LIFETIME:
//   ddog_prof_FunctionId2 (function_id) is an opaque handle into the ProfilesDictionary,
//   which is an Arc<ProfilesDictionary> that persists across all profile upload cycles
//   by design ("common to multiple profiles through time").  Cached values are therefore
//   valid for the entire process lifetime, subject to two invalidation events:
//     1. fork()  — child process gets a fresh ProfilesDictionary; all IDs are stale.
//     2. GC of the code object itself (see below).
//
// GC SAFETY with weak references:
//   Each entry stores a PyWeakReference* (owned reference) to its code object.
//   On get(), PyWeakref_GET_OBJECT() is checked:
//     - alive (not Py_None) -> return cached function_id
//     - dead  (== Py_None)  -> evict entry, Py_DECREF the weakref, return nullopt
//   This prevents a new code object allocated at the same address from receiving
//   a stale (wrong-function) ID.
//
// THREAD SAFETY:
//   All public methods acquire mtx_ internally.  Callers need only hold the GIL
//   (which all current callers of push_pyframes/push_pytraceback do).
//   Do not call allocating Python APIs while holding mtx_.
//
// GIL REQUIREMENT:
//   Every public method must be called with the GIL held.
//   PyWeakref_NewRef, PyWeakref_GET_OBJECT, and Py_DECREF are not safe without it.
class CodeObjectFunctionCache
{
  public:
    static CodeObjectFunctionCache& instance();

    // Returns cached function_id if the code object is still alive, nullopt otherwise.
    // Evicts the entry if the code object has been GC'd (lazy tombstone).
    std::optional<function_id> get(PyObject* code);

    // Stores fn_id for code, creating a weak reference.
    // Silently skips if PyWeakref_NewRef fails (should not happen for PyCodeObject).
    // If two threads race on a cache miss for the same key, the second insert is a no-op.
    void insert(PyObject* code, function_id fn_id);

    // Py_DECREF all held weak references (if interpreter is still alive) and clear the map.
    // Call when the ProfilesDictionary is about to be released.
    void clear();

    // Reinitialise the mutex via placement-new, then call clear().
    // Must be called in postfork_child() before release_profiles_dictionary().
    void postfork_child();

  private:
    CodeObjectFunctionCache() = default;
    ~CodeObjectFunctionCache() = default;
    CodeObjectFunctionCache(const CodeObjectFunctionCache&) = delete;
    CodeObjectFunctionCache& operator=(const CodeObjectFunctionCache&) = delete;

    struct Entry
    {
        PyObject* weak_ref; // owned PyWeakReference* to the code object
        function_id fn_id;
    };

    std::mutex mtx_;
    std::unordered_map<uintptr_t, Entry> map_;
};

} // namespace Datadog
