#include <library_config_interface.hpp>
#include <library_config.hpp>
#include <string_view>
#include <vector>
#include <unordered_map>

// A global instance of the library config is created here.
Datadog::LibraryConfig libconfig;

void 
libraryconfig_set_args(const std::vector<std::string_view>& args) // cppcheck-suppress unusedFunction
{
    libconfig.set_args(args);
}

void 
libraryconfig_set_envp(const std::vector<std::string_view>& envp) // cppcheck-suppress unusedFunction
{
    libconfig.set_envp(envp);
}

Datadog::ConfigVec
libraryconfig_generate_config(bool debug_logs) // cppcheck-suppress unusedFunction
{
    return libconfig.generate_config(debug_logs);
}
