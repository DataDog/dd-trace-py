// Fuzz harness: ThreadInfo::unwind

#include "fuzz_common.h"

#include <echion/echion_sampler.h>
#include <echion/tasks.h>

extern "C" int
LLVMFuzzerTestOneInput(const uint8_t* data, size_t size)
{
    g_data = data;
    g_size = size;

    if (size == 0) {
        return 0;
    }

    EchionSampler echion_sampler;
    auto tid = 123;
    ThreadInfo thread(tid, tid, "name", 1234);
    PyThreadState tstate;

#if PY_VERSION_HEX >= 0x030d0000
    // This is obviously wrong, but it's a no-op if copy fails
    tstate.current_frame = reinterpret_cast<_PyInterpreterFrame*>(123456);
#elif PY_VERSION_HEX >= 0x030b0000
    _PyCFrame cframe;
    cframe.current_frame = reinterpret_cast<_PyInterpreterFrame*>(123456);
    tstate.cframe = &cframe;
#else
    tstate.frame = reinterpret_cast<PyFrameObject*>(123456);
#endif

#if PY_VERSION_HEX >= 0x030b0000
    _PyStackChunk chunk;
    chunk.previous = nullptr;
    chunk.size = 0;
    tstate.datastack_chunk = &chunk;
#endif

    auto p_loop = addr_from_u64(load_u64_le(data, size, 0));
    auto p_tstate = addr_from_u64(load_u64_le(data, size, sizeof(p_loop)));

    thread.asyncio_loop = p_loop;
    thread.tstate_addr = p_tstate;
    thread.using_uvloop = false;

    auto p_scheduled = reinterpret_cast<PyObject*>(addr_from_u64(load_u64_le(data, size, 2 * sizeof(uintptr_t))));
    echion_sampler.init_asyncio(p_scheduled, Py_None);

    // Tries to unwind_python_stack (which does nothing because we
    // have bogus data), then unwind_tasks (the one we test here)
    thread.unwind(echion_sampler, &tstate);

    g_data = nullptr;
    g_size = 0;
    return 0;
}
