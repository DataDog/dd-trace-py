// Fuzz harness: GreenletInfo::unwind
//
// Exercises greenlet frame unwinding. GreenletInfo::unwind resolves
// the greenlet's frame address (version-dependent: direct pointer,
// f_frame indirection, or cframe→current_frame), then calls
// unwind_frame to walk the frame chain. Tests stale pointer handling
// via copy_type and the version-specific frame resolution paths.

#include "fuzz_common.h"

#include <echion/echion_sampler.h>
#include <echion/greenlets.h>

extern "C" int
LLVMFuzzerTestOneInput(const uint8_t* data, size_t size)
{
    if (size < 3 * sizeof(uint64_t)) {
        return 0;
    }

    g_data = data;
    g_size = size;

    uintptr_t p0 = addr_from_u64(load_u64_le(data, size, 0));
    uintptr_t p1 = addr_from_u64(load_u64_le(data, size, 8));
    uintptr_t p2 = addr_from_u64(load_u64_le(data, size, 16));

    EchionSampler echion_sampler;

    // Build a GreenletInfo with a fuzz-derived frame pointer and unwind it.
    // GreenletInfo::unwind reads the frame via copy_type (handling stale
    // pointers gracefully), resolves version-specific frame layouts, and
    // calls unwind_frame to walk the Python frame chain.
    {
        GreenletInfo greenlet(/*id=*/42, reinterpret_cast<PyObject*>(p0), StringTable::UNKNOWN);
        PyThreadState tstate;
        std::memset(&tstate, 0, sizeof(tstate));

#if PY_VERSION_HEX >= 0x030d0000
        tstate.current_frame = reinterpret_cast<_PyInterpreterFrame*>(p1);
#elif PY_VERSION_HEX >= 0x030b0000
        tstate.cframe = reinterpret_cast<_PyCFrame*>(p1);
#else
        tstate.frame = reinterpret_cast<PyFrameObject*>(p1);
#endif

        FrameStack stack;
        greenlet.unwind(echion_sampler, reinterpret_cast<PyObject*>(p0), &tstate, stack);
    }

    // Also test with Py_None as the frame (indicates on-CPU greenlet),
    // which takes a different code path through the tstate.
    {
        GreenletInfo on_cpu_greenlet(/*id=*/43, Py_None, StringTable::UNKNOWN);
        PyThreadState tstate;
        std::memset(&tstate, 0, sizeof(tstate));

#if PY_VERSION_HEX >= 0x030d0000
        tstate.current_frame = reinterpret_cast<_PyInterpreterFrame*>(p2);
#elif PY_VERSION_HEX >= 0x030b0000
        tstate.cframe = reinterpret_cast<_PyCFrame*>(p2);
#else
        tstate.frame = reinterpret_cast<PyFrameObject*>(p2);
#endif

        FrameStack stack;
        on_cpu_greenlet.unwind(echion_sampler, Py_None, &tstate, stack);
    }

    g_data = nullptr;
    g_size = 0;
    return 0;
}
