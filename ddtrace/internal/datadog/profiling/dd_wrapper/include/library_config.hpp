#pragma once

#include <constants.hpp>
#include <libdatadog_helpers.hpp>

#include <atomic>
#include <optional>
#include <vector>
#include <string_view>
#include <string>

#include <datadog/library-config.h>
#include <datadog/common.h>

namespace Datadog {

typedef struct ConfigEntry {
    std::string key;
    std::string value;
} ConfigEntry;

typedef struct ConfigVec {
    ConfigEntry* ptr;
    size_t len;
} ConfigVec;

class LibraryConfig
{
  private:
    std::vector<std::string> _envp;
    std::vector<std::string> _args;
    ddog_Slice_CharSlice to_char_slice(const std::vector<std::string>& vec);

  public:
    // Setters -- these methods are used to set the library context
    void set_envp(const std::vector<std::string_view>& envp);
    void set_args(const std::vector<std::string_view>& args);

    // Main method
    ConfigVec generate_config(bool debug_logs);
};

} // namespace Datadog
