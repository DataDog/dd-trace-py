#pragma once

#include <string_view>

#ifdef __cplusplus
extern "C"
{
#endif
    void code_provenance_set_file_path(std::string_view file_path);
#ifdef __cplusplus
} // extern "C"
#endif
