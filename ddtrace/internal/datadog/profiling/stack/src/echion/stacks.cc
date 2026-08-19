#include <echion/stacks.h>

#include <echion/echion_sampler.h>
#include <unordered_set>

#include "dd_wrapper/include/profiler_state.hpp"

void
FrameStack::render(EchionSampler& echion, TruncationStatus truncation)
{
    auto& renderer = echion.renderer();
    auto& registry = Datadog::ProfilerState::get().native_call_registry;

    for (auto it = this->begin(); it != this->end(); ++it) {
        auto& frame = *it;

        // Inject native frame BEFORE its Python caller.
        // sys.monitoring reports instruction offsets in bytes, while the sampler computes
        // frame.lasti in _Py_CODEUNIT units. Convert to bytes for the registry lookup.
        if (frame.code_object != 0 && frame.lasti >= 0) {
            int offset_bytes = frame.lasti * static_cast<int>(sizeof(_Py_CODEUNIT));
            auto maybe_entry = registry.lookup(frame.code_object, offset_bytes, frame.first_lineno);
            if (maybe_entry) {
                const auto& entry = maybe_entry->get();
                renderer.render_native_frame(entry.name, entry.module);
            }
        }

        renderer.render_frame(frame);
    }

    if (truncation == TruncationStatus::Truncated) {
        renderer.render_truncated();
    }
}

// Unwind Python frames starting from frame_addr and push them onto stack.
// @param seen_frames: Cycle-detection set. Cleared on entry; capacity is reused
//                     by callers (typically EchionSampler::seen_frames_scratch).
// @param max_frames_to_add: Maximum number of frames to add during this walk.
// @param detect_truncation: Whether to probe for another reportable frame after reaching the limit.
// @return: Number of frames added and the truncation detection status.
UnwindResult
unwind_frame(EchionSampler& echion,
             PyObject* frame_addr,
             FrameStack& stack,
             std::unordered_set<PyObject*>& seen_frames,
             size_t max_frames_to_add,
             bool detect_truncation)
{
    seen_frames.clear();
    if (!detect_truncation && (max_frames_to_add == 0 || stack.size() >= MAX_TASK_FRAMES)) {
        return UnwindResult{};
    }

    UnwindResult result;
    size_t frames_probed_after_limit = 0;
    PyObject* current_frame_addr = frame_addr;
    while (current_frame_addr != NULL) {
        const bool at_limit = result.frames_added >= max_frames_to_add || stack.size() >= MAX_TASK_FRAMES;
        if (at_limit) {
            if (!detect_truncation || frames_probed_after_limit >= MAX_TASK_FRAMES) {
                return result;
            }
            frames_probed_after_limit++;
        }
        if (seen_frames.contains(current_frame_addr)) {
            return result;
        }

        seen_frames.insert(current_frame_addr);

#if PY_VERSION_HEX >= 0x030b0000
        auto maybe_frame = Frame::read(echion,
                                       reinterpret_cast<_PyInterpreterFrame*>(current_frame_addr),
                                       reinterpret_cast<_PyInterpreterFrame**>(&current_frame_addr));
#else
        auto maybe_frame = Frame::read(echion, current_frame_addr, &current_frame_addr);
#endif
        if (!maybe_frame) {
            return result;
        }

        if (maybe_frame->get().name == StringTable::C_FRAME) {
            continue;
        }

        // When reporting truncation, confirm that the bounded lookahead found a
        // reportable frame so terminal C/interpreter frames do not produce a false marker.
        if (at_limit) {
            result.truncation = TruncationStatus::Truncated;
            return result;
        }

        stack.push_back(maybe_frame->get());
        result.frames_added++;
    }

    if (detect_truncation) {
        result.truncation = TruncationStatus::NotTruncated;
    }
    return result;
}

// Convenience variant that owns its own scratch set and delegates to the
// primary overload above. For callers without a reusable scratch set to share.
UnwindResult
unwind_frame(EchionSampler& echion,
             PyObject* frame_addr,
             FrameStack& stack,
             size_t max_frames_to_add,
             bool detect_truncation)
{
    std::unordered_set<PyObject*> local_seen_frames;
    return unwind_frame(echion, frame_addr, stack, local_seen_frames, max_frames_to_add, detect_truncation);
}

UnwindResult
unwind_python_stack(EchionSampler& echion, PyThreadState* tstate, FrameStack& stack, size_t max_frames)
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
        return UnwindResult{};

    PyObject* frame_addr = reinterpret_cast<PyObject*>(cframe.current_frame);
#else // Python < 3.11
    PyObject* frame_addr = reinterpret_cast<PyObject*>(tstate->frame);
#endif
    return unwind_frame(echion, frame_addr, stack, echion.seen_frames_scratch(), max_frames, true);
}
