#include "receiver_interface.h"

#include <unistd.h>

#include "datadog/crashtracker.h"
#include "datadog/profiling.h"

// This has to be a .cpp instead of a .c file because
// * crashtracker.c, which builds the receiver binary, has to link to libdatadog-internal interfaces
// * Those interfaces are C interfaces, but they are not exposed in libdd_wrapper.so
// * Exposing them separately increases dist size
// * This forces crashtracker_receiver_entry to be within the same .so
// * The build for libdd_wrapper.so uses C++-specific flags which are incompatible with C
// * It's annoying to split the build into an object library for just one file

bool
crashtracker_receiver_entry() // cppcheck-suppress unusedFunction
{
    // Assumes that this will be called only in the receiver binary, which is a
    // fresh process
    ddog_VoidResult new_result = ddog_crasht_receiver_entry_point_stdin();
    if (new_result.tag != DDOG_VOID_RESULT_OK) {
        ddog_CharSlice message = ddog_Error_message(&new_result.err);

        //`write` may not write what we want it to write, but there's nothing we can do about it,
        // so ignore the return
        int n = write(STDERR_FILENO, message.ptr, message.len);
        (void)n;

        ddog_Error_drop(&new_result.err);
        return false;
    }
    return true;
}
