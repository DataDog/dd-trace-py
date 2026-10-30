// Finds CPython's AsyncioDebug table through _asyncio's module state (Python 3.14+).

// Included first: it defines Py_BUILD_CORE before Python.h, which the internal headers below need.
#include <echion/echion_sampler.h>

#include <echion/cpython/asyncio_discovery.h>
#include <echion/vm.h>

#if PY_VERSION_HEX >= 0x030e0000

#include <cstddef>
#include <cstdint>
#include <cstring>

#include <internal/pycore_moduleobject.h>

std::optional<AsyncioOffsets>
discover_asyncio_offsets(PyObject* asyncio_module)
{
    if (asyncio_module == nullptr || !PyModule_Check(asyncio_module)) {
        return std::nullopt;
    }
    PyModuleDef* def = PyModule_GetDef(asyncio_module);
    if (def == nullptr || def->m_name == nullptr || std::strcmp(def->m_name, "_asyncio") != 0 ||
        def->m_size != static_cast<Py_ssize_t>(sizeof(AsyncioStateMirror))) {
        return std::nullopt;
    }
    // The module's exec sets md_state with a plain store, which another thread can be doing right now on free-threaded
    // builds, so the slot is read with a syscall.
    uintptr_t state = 0;
    if (!read_own_memory(
          reinterpret_cast<uintptr_t>(asyncio_module) + offsetof(PyModuleObject, md_state), &state, sizeof(state))) {
        return std::nullopt;
    }
    if (state == 0 || state % alignof(AsyncioStateMirror) != 0) {
        return std::nullopt;
    }

    // In bounds because m_size matched.
    uintptr_t table_address = 0;
    if (!read_own_memory(state + offsetof(AsyncioStateMirror, debug_offsets), &table_address, sizeof(table_address))) {
        return std::nullopt;
    }

    auto offsets = read_asyncio_debug_offsets_at(table_address, EchionSampler::asyncio_mirror_task_layout());
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
    if (!asyncio_offsets_match_live_heads(*offsets, reinterpret_cast<uintptr_t>(tstate), interp_base)) {
        return std::nullopt;
    }
    return offsets;
}

#endif
