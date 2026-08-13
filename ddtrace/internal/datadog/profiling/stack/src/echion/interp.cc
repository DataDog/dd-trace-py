#include <echion/interp.h>

bool
for_each_interp(_PyRuntimeState* runtime, const std::function<void(InterpreterInfo& interp)>& callback)
{
    bool snapshot_complete = true;

    // Limit interpreter iteration to prevent infinite loops from cycles or corrupted memory.
    // This limit is based on CPython's tachyon profiler (256) and should be more than
    // enough for realistic use cases (most applications use 1 interpreter).
    const size_t MAX_INTERPRETERS = 256;

    char* interp_addr = reinterpret_cast<char*>(runtime->interpreters.head);
    char* prev_interp_addr = nullptr;

    // Safety: prevent infinite loops from cycles or corrupted interpreter linked lists
    for (size_t iteration_count = 0; iteration_count < MAX_INTERPRETERS && interp_addr != NULL; ++iteration_count) {

        // Cycle detection: if we didn't advance from previous iteration, we're stuck
        if (prev_interp_addr != nullptr && interp_addr == prev_interp_addr) {
            return false; // Cycle detected or failed to advance
        }
        prev_interp_addr = interp_addr;

        InterpreterInfo interpreter_info = { 0 };
#if PY_VERSION_HEX >= 0x030e0000
        snapshot_complete &= !copy_type(interp_addr + offsetof(PyInterpreterState, _code_object_generation),
                                        interpreter_info.code_object_generation);
#endif

        // Always read next pointer first - we need it to advance
        if (copy_type(interp_addr + offsetof(PyInterpreterState, next), interpreter_info.next))
            return false; // Can't read next, can't advance - stop iteration

        if (copy_type(interp_addr + offsetof(PyInterpreterState, id), interpreter_info.id)) {
            snapshot_complete = false;
            interp_addr = reinterpret_cast<char*>(interpreter_info.next);
            continue;
        }

#if PY_VERSION_HEX >= 0x030b0000
        if (copy_type(interp_addr + offsetof(PyInterpreterState, threads.head), interpreter_info.tstate_head))
#else
        if (copy_type(interp_addr + offsetof(PyInterpreterState, tstate_head), interpreter_info.tstate_head))
#endif
        {
            snapshot_complete = false;
            interp_addr = reinterpret_cast<char*>(interpreter_info.next);
            continue;
        }

        callback(interpreter_info);

        // Move to next interpreter
        interp_addr = reinterpret_cast<char*>(interpreter_info.next);
    }

    return snapshot_complete && interp_addr == NULL;
}
