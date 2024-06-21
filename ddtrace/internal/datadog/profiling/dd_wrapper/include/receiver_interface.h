#pragma once

#include <stdbool.h>
#include <unistd.h>

#include "datadog/profiling.h"

bool
crashtracker_receiver_entry()
{
    // Assumes that this will be called only in the receiver binary, which is a
    // fresh process
    ddog_prof_CrashtrackerResult new_result = ddog_prof_Crashtracker_receiver_entry_point();
    if (new_result.tag != DDOG_PROF_CRASHTRACKER_RESULT_OK) {
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
