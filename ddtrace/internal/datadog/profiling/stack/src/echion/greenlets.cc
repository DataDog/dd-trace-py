#include <echion/greenlets.h>

#include <echion/echion_sampler.h>

void
GreenletInfo::unwind(EchionSampler& echion, PyObject* cur_frame, PyThreadState* tstate, FrameStack& stack)
{
    PyObject* frame_addr = NULL;
#if PY_VERSION_HEX >= 0x030d0000
    if (cur_frame == Py_None) {
        frame_addr = reinterpret_cast<PyObject*>(tstate->current_frame);
    } else {
        // cur_frame may be a stale pointer if the greenlet finished between
        // the snapshot (Phase 1) and unwind (Phase 2). Use copy_type to read
        // safely instead of dereferencing directly.
        struct _frame frame_copy;
        if (copy_type(cur_frame, frame_copy))
            return;

        frame_addr = reinterpret_cast<PyObject*>(frame_copy.f_frame);
    }
#elif PY_VERSION_HEX >= 0x030b0000
    if (cur_frame == Py_None) {
        _PyCFrame cframe;
        _PyCFrame* cframe_addr = tstate->cframe;
        if (copy_type(cframe_addr, cframe)) {
            return;
        }

        frame_addr = reinterpret_cast<PyObject*>(cframe.current_frame);
    } else {
        // cur_frame may be a stale pointer if the greenlet finished between
        // the snapshot (Phase 1) and unwind (Phase 2). Use copy_type to read
        // safely instead of dereferencing directly.
        struct _frame frame_copy;
        if (copy_type(cur_frame, frame_copy))
            return;
        frame_addr = reinterpret_cast<PyObject*>(frame_copy.f_frame);
    }
#else // Python < 3.11
    frame_addr = cur_frame == Py_None ? reinterpret_cast<PyObject*>(tstate->frame) : cur_frame;
#endif
    unwind_frame(echion, frame_addr, stack);

    stack.push_back(Frame::get(echion, name));
}
