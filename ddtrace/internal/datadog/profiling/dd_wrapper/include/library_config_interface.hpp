#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string_view>
#include <vector>
#include <unordered_map>
#include <library_config.hpp>
#include <datadog/common.h>

#ifdef __cplusplus
extern "C"
{
#endif
    void libraryconfig_set_args(const std::vector<std::string_view>& args);
    void libraryconfig_set_envp(const std::vector<std::string_view>& envp);

    Datadog::ConfigVec libraryconfig_generate_config(bool debug_logs);
#ifdef __cplusplus
} // extern "C"

#endif
