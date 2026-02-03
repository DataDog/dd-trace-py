#include <echion/greenlets.h>

#include <echion/echion_sampler.h>

int
GreenletInfo::unwind(PyObject* cur_frame, PyThreadState* tstate, FrameStack& stack, EchionSampler& echion)
{
    PyObject* frame_addr = NULL;
#if PY_VERSION_HEX >= 0x030d0000
    frame_addr = cur_frame == Py_None
                   ? reinterpret_cast<PyObject*>(tstate->current_frame)
                   : reinterpret_cast<PyObject*>(reinterpret_cast<struct _frame*>(cur_frame)->f_frame);
#elif PY_VERSION_HEX >= 0x030b0000
    if (cur_frame == Py_None) {
        _PyCFrame cframe;
        _PyCFrame* cframe_addr = tstate->cframe;
        if (copy_type(cframe_addr, cframe))
            // TODO: Invalid cur_frame
            return 0;

        frame_addr = reinterpret_cast<PyObject*>(cframe.current_frame);
    } else {
        frame_addr = reinterpret_cast<PyObject*>(reinterpret_cast<struct _frame*>(cur_frame)->f_frame);
    }

#else // Python < 3.11
    frame_addr = cur_frame == Py_None ? reinterpret_cast<PyObject*>(tstate->frame) : cur_frame;
#endif
    auto count = unwind_frame(frame_addr, stack, echion);

    stack.push_back(Frame::get(name, echion.frame_cache()));

    return count + 1; // We add an extra count for the frame with the greenlet
                      // name.
}