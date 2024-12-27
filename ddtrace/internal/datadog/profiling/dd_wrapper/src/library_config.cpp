#include <iostream>
#include <string>
#include <string_view>
#include <sys/stat.h>
#include <vector>
#include <library_config.hpp>
#include <datadog/library-config.h>
#include <datadog/common.h>

ddog_Slice_CharSlice 
Datadog::LibraryConfig::to_char_slice(const std::vector<std::string>& vec) {
    ddog_CharSlice* slices = new ddog_CharSlice[vec.size()];
    for (size_t i = 0; i < vec.size(); i++) {
        slices[i] = ddog_CharSlice{vec[i].data(), vec[i].size()};
    }
    ddog_Slice_CharSlice result = {slices, vec.size()};
    return result;
}

void
Datadog::LibraryConfig::set_envp(const std::vector<std::string_view>& envp)
{
    // Copy string_views to strings to avoid garbage collection
    std::vector<std::string> new_envp;
    for (size_t i = 0; i < envp.size(); i++) {
        if(!envp[i].empty()) {
            new_envp.push_back(std::string(envp[i]));
        }
    }
    _envp = std::move(new_envp);
}

void
Datadog::LibraryConfig::set_args(const std::vector<std::string_view>& args)
{
    // Copy string_views to strings to avoid garbage collection
    std::vector<std::string> new_args;
    for (size_t i = 0; i < args.size(); i++) {
        if(!args[i].empty()) {
            new_args.push_back(std::string(args[i]));
        }
    }
    _args = std::move(new_args);
}

Datadog::ConfigVec
Datadog::LibraryConfig::generate_config(bool debug_logs)
{
    // Build process info struct
    ddog_ProcessInfo process_info{
        .args = to_char_slice(_args),
        .envp = to_char_slice(_envp),
        .language = DDOG_CHARSLICE_C("python"),
    };

    // Build config struct
    ddog_Configurator *configurator = ddog_library_configurator_new(debug_logs);

    // Compute configs
    ddog_Result_VecLibraryConfig config_result = ddog_library_configurator_get(configurator, process_info);

    // Check for errors
    if (config_result.tag == DDOG_RESULT_VEC_LIBRARY_CONFIG_ERR_VEC_LIBRARY_CONFIG) {
        ddog_Error err = config_result.err;
        if (debug_logs) {
            auto ddog_err = ddog_Error_message(&err);
            std::string err_msg;
            std::cerr << err_msg.assign(ddog_err.ptr, ddog_err.ptr + ddog_err.len) << std::endl;
        }
        ddog_Error_drop(&err);
        return Datadog::ConfigVec{};
    }

    // Format to new type
    ddog_Vec_LibraryConfig configs = config_result.ok;
    Datadog::ConfigVec result{
        .ptr = new Datadog::ConfigEntry[configs.len],
        .len = configs.len,
    };

    for (size_t i = 0; i < configs.len; i++) {
        const ddog_LibraryConfig *cfg = &configs.ptr[i];
        ddog_CStr name = ddog_library_config_name_to_env(cfg->name);
        const ddog_CString value = cfg->value;

        Datadog::ConfigEntry* new_entry = new Datadog::ConfigEntry{
            .key = name.ptr,
            .value = value.ptr,
        };

        // Add to result
        result.ptr[i] = *new_entry;
    };

    return result;
}
