// Finds CPython's AsyncioDebug table through _asyncio's module state (Python 3.14+).
//
// _asyncio keeps a pointer to the table in the last field of its module state (asyncio_state.debug_offsets), so the
// compiler cannot drop the table. Echion mirrors that struct (AsyncioStateMirror in cpython/tasks.h) the same way it
// mirrors TaskObj, and checks the mirror's size against the module's m_size before reading the one field it needs.
// Everything read from the state onwards goes through read_own_memory and is validated in asyncio_debug.cc.

// First, so Python.h is included with Py_BUILD_CORE as the CPython internal headers it pulls in require.
#include <echion/echion_sampler.h>

#include <echion/cpython/asyncio_discovery.h>

#if PY_VERSION_HEX >= 0x030e0000

#include <cstddef>
#include <cstdint>
#include <cstring>

std::optional<AsyncioOffsets>
discover_asyncio_offsets(PyObject* asyncio_module)
{
    // PyModule_GetDef and PyModule_GetState only read fields of the module object: no Python code, no locks.
    if (asyncio_module == nullptr || !PyModule_Check(asyncio_module)) {
        return std::nullopt;
    }
    PyModuleDef* def = PyModule_GetDef(asyncio_module);
    if (def == nullptr || def->m_name == nullptr || std::strcmp(def->m_name, "_asyncio") != 0 ||
        def->m_size != static_cast<Py_ssize_t>(sizeof(AsyncioStateMirror))) {
        PyErr_Clear();
        return std::nullopt;
    }
    void* state = PyModule_GetState(asyncio_module);
    if (state == nullptr || reinterpret_cast<uintptr_t>(state) % alignof(AsyncioStateMirror) != 0) {
        PyErr_Clear();
        return std::nullopt;
    }

    // In bounds because m_size matched. Copied through the kernel rather than loaded: if a same-size layout change put
    // task_name_counter here, a plain load would race with its atomic updates on free-threaded builds.
    uintptr_t table_address = 0;
    if (!read_own_memory(static_cast<const char*>(state) + offsetof(AsyncioStateMirror, debug_offsets),
                         &table_address,
                         sizeof(table_address))) {
        return std::nullopt;
    }

    auto offsets =
      read_asyncio_debug_offsets_at(table_address, EchionSampler::asyncio_mirror_task_layout(), read_own_memory);
    if (!offsets) {
        return std::nullopt;
    }

    PyThreadState* tstate = PyThreadState_Get();
#ifdef Py_GIL_DISABLED
    // Another thread can be moving its tasks into the interpreter list right now (llist_concat under
    // interp->asyncio_tasks_lock), so only the calling thread's own list is checked.
    const uintptr_t interp_base = 0;
#else
    const uintptr_t interp_base = reinterpret_cast<uintptr_t>(PyThreadState_GetInterpreter(tstate));
#endif
    if (!asyncio_offsets_match_live_heads(
          *offsets, reinterpret_cast<uintptr_t>(tstate), interp_base, read_own_memory)) {
        return std::nullopt;
    }
    return offsets;
}

#endif
