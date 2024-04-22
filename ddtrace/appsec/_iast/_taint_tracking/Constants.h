#pragma once
#define PY_MODULE_NAME "ddtrace.appsec._iast._taint_tracking._native"
#define RANGE_START long
#define RANGE_LENGTH long
#define MSG_ERROR_N_PARAMS "[IAST] Invalid number of params"
#define MSG_ERROR_SET_RANGES "[IAST] Set ranges error: Empty ranges or Tainted Map isn't initialized"
#define MSG_ERROR_GET_RANGES_TYPE "[IAST] Get ranges error: Invalid type of candidate_text variable"
#define MSG_ERROR_TAINT_MAP "[IAST] Tainted Map isn't initialized"