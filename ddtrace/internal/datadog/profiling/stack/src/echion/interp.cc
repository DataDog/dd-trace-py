#include <echion/interp.h>

#include <algorithm>
#include <array>

InterpreterTraversalResult
for_each_interp(_PyRuntimeState* runtime, const std::function<void(InterpreterInfo& interp)>& callback)
{
    InterpreterTraversalResult result;
    std::array<char*, MAX_INTERPRETERS> visited;
    size_t visited_count = 0;

    char* interp_addr = reinterpret_cast<char*>(runtime->interpreters.head);
    if (interp_addr == nullptr) {
        result.add(InterpreterTraversalIssue::EmptyInventory);
        return result;
    }

    // Keep a fixed-size address inventory so arbitrary cycles are detected without allocating
    // from the sampling thread. The hard bound also protects against corrupted linked lists.
    while (interp_addr != nullptr && visited_count < MAX_INTERPRETERS) {
        if (std::find(visited.begin(), visited.begin() + visited_count, interp_addr) !=
            visited.begin() + visited_count) {
            result.add(InterpreterTraversalIssue::CycleDetected);
            return result;
        }
        visited[visited_count++] = interp_addr;

        InterpreterInfo interpreter_info = { 0 };
        interpreter_info.interp = reinterpret_cast<PyInterpreterState*>(interp_addr);
#if PY_VERSION_HEX >= 0x030e0000
        if (copy_type(interp_addr + runtime->debug_offsets.interpreter_state.code_object_generation,
                      interpreter_info.code_object_generation)) {
            result.add(InterpreterTraversalIssue::CodeObjectGenerationUnreadable);
        }
#endif

        // The next pointer is required to continue traversing the list.
        if (copy_type(interp_addr + offsetof(PyInterpreterState, next), interpreter_info.next)) {
            result.add(InterpreterTraversalIssue::NextUnreadable);
            return result;
        }

        if (copy_type(interp_addr + offsetof(PyInterpreterState, id), interpreter_info.id)) {
            result.add(InterpreterTraversalIssue::IdUnreadable);
            interp_addr = reinterpret_cast<char*>(interpreter_info.next);
            continue;
        }

#if PY_VERSION_HEX >= 0x030b0000
        if (copy_type(interp_addr + offsetof(PyInterpreterState, threads.head), interpreter_info.tstate_head))
#else
        if (copy_type(interp_addr + offsetof(PyInterpreterState, tstate_head), interpreter_info.tstate_head))
#endif
        {
            result.add(InterpreterTraversalIssue::ThreadHeadUnreadable);
            interp_addr = reinterpret_cast<char*>(interpreter_info.next);
            continue;
        }

        callback(interpreter_info);
        interp_addr = reinterpret_cast<char*>(interpreter_info.next);
    }

    if (interp_addr != nullptr) {
        result.add(InterpreterTraversalIssue::LimitExceeded);
    }
    return result;
}
