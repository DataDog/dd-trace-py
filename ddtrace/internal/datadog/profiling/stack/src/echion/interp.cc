#include <echion/interp.h>

void
for_each_interp(_PyRuntimeState* runtime, std::function<void(InterpreterInfo& interp)> callback)
{
    InterpreterInfo interpreter_info = { 0 };

    for (char* interp_addr = reinterpret_cast<char*>(runtime->interpreters.head); interp_addr != NULL;
         interp_addr = reinterpret_cast<char*>(interpreter_info.next)) {
        if (copy_type(interp_addr + offsetof(PyInterpreterState, id), interpreter_info.id))
            continue;

#if PY_VERSION_HEX >= 0x030b0000
        if (copy_type(interp_addr + offsetof(PyInterpreterState, threads.head), interpreter_info.tstate_head))
#else
        if (copy_type(interp_addr + offsetof(PyInterpreterState, tstate_head), interpreter_info.tstate_head))
#endif
            continue;

        if (copy_type(interp_addr + offsetof(PyInterpreterState, next), interpreter_info.next))
            continue;

        callback(interpreter_info);
    };
}
