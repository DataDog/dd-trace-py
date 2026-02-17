#include <echion/stacks.h>

#include <echion/echion_sampler.h>
#include <unordered_set>

#include <echion/echion_sampler.h>

void
FrameStack::render(EchionSampler& echion)
{
    auto& renderer = echion.renderer();
    bool pushed = false;
    for (auto it = this->rbegin(); it != this->rend(); ++it) {
#if PY_VERSION_HEX >= 0x030c0000
        if ((*it).get().is_entry)
            // This is a shim frame so we skip it.
            continue;
#endif
        auto& frame = it->get();
        if (frame.is_c_frame && !pushed) {
            renderer.render_frame(frame);
            pushed = true;
        } else if (!frame.is_c_frame) {
            renderer.render_frame(frame);
            pushed = true;
        }
    }
}

// Unwind Python frames starting from frame_addr and push them onto stack.
// @param max_depth: Maximum number of frames to unwind. Defaults to max_frames.
// @return: Number of frames added to the stack.
size_t
unwind_frame(EchionSampler& echion, PyObject* frame_addr, FrameStack& stack, size_t max_depth)
{
    std::unordered_set<PyObject*> seen_frames; // Used to detect cycles in the stack
    size_t count = 0;

    PyObject* current_frame_addr = frame_addr;
    while (current_frame_addr != NULL && stack.size() < max_frames) {
        if (seen_frames.contains(current_frame_addr))
            break;

        seen_frames.insert(current_frame_addr);

#if PY_VERSION_HEX >= 0x030b0000
        auto maybe_frame = Frame::read(echion,
                                       reinterpret_cast<_PyInterpreterFrame*>(current_frame_addr),
                                       reinterpret_cast<_PyInterpreterFrame**>(&current_frame_addr));
#else
        auto maybe_frame = Frame::read(echion, current_frame_addr, &current_frame_addr);
#endif
        if (!maybe_frame) {
            break;
        }

        auto& frame = maybe_frame->get();
        if (frame.name == StringTable::C_FRAME) {
            continue;
        }

        // If this is the leaf frame and it's in a C call, push the synthetic C frame first
        if (stack.empty() && frame.in_c_call) {
            auto maybe_c_frame = echion.frame_cache().lookup(frame.c_frame_key);
            if (maybe_c_frame) {
                stack.push_back(*maybe_c_frame);
            }
        }

        stack.push_back(frame);
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
    stack.clear();
#if PY_VERSION_HEX >= 0x030b0000
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
    unwind_frame(echion, frame_addr, stack);
}
