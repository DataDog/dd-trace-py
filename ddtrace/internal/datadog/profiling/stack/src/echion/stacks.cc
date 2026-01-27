#include <echion/stacks.h>

#include <echion/echion_sampler.h>

#include <unordered_set>

// Unwind Python frames starting from frame_addr and push them onto stack.
// @param max_depth: Maximum number of frames to unwind. Defaults to max_frames.
// @return: Number of frames added to the stack.
#if PY_VERSION_HEX >= 0x030b0000
size_t
unwind_frame(StackChunk* stack_chunk, PyObject* frame_addr, FrameStack& stack, size_t max_depth)
#else
size_t
unwind_frame(PyObject* frame_addr, FrameStack& stack, size_t max_depth)
#endif
{
    std::unordered_set<PyObject*> seen_frames; // Used to detect cycles in the stack
    size_t count = 0;

    PyObject* current_frame_addr = frame_addr;
    while (current_frame_addr != NULL && stack.size() < max_frames) {
        if (seen_frames.contains(current_frame_addr))
            break;

        seen_frames.insert(current_frame_addr);

#if PY_VERSION_HEX >= 0x030b0000
        auto maybe_frame = Frame::read(stack_chunk,
                                       reinterpret_cast<_PyInterpreterFrame*>(current_frame_addr),
                                       reinterpret_cast<_PyInterpreterFrame**>(&current_frame_addr));
#else
        auto maybe_frame = Frame::read(current_frame_addr, &current_frame_addr);
#endif
        if (!maybe_frame) {
            break;
        }

        if (maybe_frame->get().name == StringTable::C_FRAME) {
            continue;
        }

        stack.push_back(*maybe_frame);
        count++;

        if (count >= max_depth) {
            break;
        }
    }

    return count;
}

void
unwind_python_stack(EchionSampler& echion, PyThreadState* tstate, FrameStack& stack)
{
#if PY_VERSION_HEX < 0x030b0000
    // Used only for Python 3.11+ to get StackChunk
    (void)echion;
#endif
    stack.clear();
#if PY_VERSION_HEX >= 0x030b0000
    auto& stack_chunk = echion.stack_chunk();
    if (stack_chunk == nullptr) {
        stack_chunk = std::make_unique<StackChunk>();
    }

    if (!stack_chunk->update(reinterpret_cast<_PyStackChunk*>(tstate->datastack_chunk))) {
        stack_chunk = nullptr;
    }
#endif

#if PY_VERSION_HEX >= 0x030d0000
    PyObject* frame_addr = reinterpret_cast<PyObject*>(tstate->current_frame);
#elif PY_VERSION_HEX >= 0x030b0000
    _PyCFrame cframe;
    _PyCFrame* cframe_addr = tstate->cframe;
    if (copy_type(cframe_addr, cframe))
        // TODO: Invalid frame
        return;

    PyObject* frame_addr = reinterpret_cast<PyObject*>(cframe.current_frame);
#else // Python < 3.11
    PyObject* frame_addr = reinterpret_cast<PyObject*>(tstate->frame);
#endif
#if PY_VERSION_HEX >= 0x030b0000
    unwind_frame(stack_chunk.get(), frame_addr, stack);
#else
    unwind_frame(frame_addr, stack);
#endif
}
