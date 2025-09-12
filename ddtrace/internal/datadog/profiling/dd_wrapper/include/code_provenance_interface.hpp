#pragma once

#include <string_view>

#ifdef __cplusplus
extern "C"
{
#endif
    void code_provenance_set_json_str(std::string_view json_str);
#ifdef __cplusplus
} // extern "C"
#endif
