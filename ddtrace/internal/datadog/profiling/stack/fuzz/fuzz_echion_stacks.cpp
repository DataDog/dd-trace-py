// Fuzz harness: unwind_frame + unwind_python_stack
//
// Exercises the full frame/stack unwinding pipeline. unwind_frame walks a
// chain of frames (up to 16 deep); unwind_python_stack starts from a
// locally-constructed PyThreadState with fuzz-derived remote pointers.

#include "fuzz_common.h"

#include <echion/echion_sampler.h>
#include <echion/stacks.h>

extern "C" int
LLVMFuzzerTestOneInput(const uint8_t* data, size_t size)
{
    g_data = data;
    g_size = size;

    if (size == 0) {
        return 0;
    }

    uintptr_t p0 = addr_from_u64(load_u64_le(data, size, 0));
    uintptr_t p1 = addr_from_u64(load_u64_le(data, size, 8));
    uintptr_t p2 = addr_from_u64(load_u64_le(data, size, 16));

    EchionSampler echion_sampler;

    // unwind_frame — walks a chain of frames from remote memory (up to 16 deep);
    // exercises Frame::read, code object resolution, cycle detection, and
    // varint line table decoding.
    {
        FrameStack fuzz_stack;
        (void)unwind_frame(echion_sampler, reinterpret_cast<PyObject*>(p0), fuzz_stack, 16);
    }

    // unwind_python_stack — full stack unwind pipeline from a locally-constructed
    // PyThreadState with fuzz-derived remote pointers.
    {
        PyThreadState tstate;
        std::memset(&tstate, 0, sizeof(tstate));
#if PY_VERSION_HEX >= 0x030d0000
        tstate.current_frame = reinterpret_cast<_PyInterpreterFrame*>(p1);
#elif PY_VERSION_HEX >= 0x030b0000
        tstate.cframe = reinterpret_cast<_PyCFrame*>(p1);
#else
        tstate.frame = reinterpret_cast<PyFrameObject*>(p1);
#endif
#if PY_VERSION_HEX >= 0x030b0000
        tstate.datastack_chunk = reinterpret_cast<_PyStackChunk*>(p2);
#endif
        FrameStack fuzz_stack;
        unwind_python_stack(echion_sampler, &tstate, fuzz_stack);
    }

    g_data = nullptr;
    g_size = 0;
    return 0;
}
