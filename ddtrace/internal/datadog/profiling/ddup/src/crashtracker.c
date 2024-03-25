// Copyright 2024-Present Datadog, Inc. https://www.datadoghq.com/
// SPDX-License-Identifier: Apache-2.0

#include <datadog/common.h>
#include <datadog/profiling.h>
#include <stdio.h>
#include <stdlib.h>

int main(void) {
  ddog_prof_Profile_Result new_result = ddog_prof_Crashtracker_receiver_entry_point();
  if (new_result.tag != DDOG_PROF_PROFILE_NEW_RESULT_OK) {
    ddog_CharSlice message = ddog_Error_message(&new_result.err);
    fprintf(stderr, "%*s", (int)message.len, message.ptr);
    ddog_Error_drop(&new_result.err);
    exit(EXIT_FAILURE);
  }
  return 0;
}
