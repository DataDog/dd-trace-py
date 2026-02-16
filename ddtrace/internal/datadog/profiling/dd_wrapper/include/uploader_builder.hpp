#pragma once

#include "constants.hpp"
#include "uploader.hpp"

#include <string>
#include <string_view>
#include <variant>

namespace Datadog {

// UploaderBuilder provides static methods to configure and build an Uploader.
// Configuration state is stored in the Ddup singleton.
class UploaderBuilder
{
    static constexpr std::string_view language{ g_language_name };
    static constexpr std::string_view family{ g_language_name };

  public:
    static void set_env(std::string_view _dd_env);
    static void set_service(std::string_view _service);
    static void set_version(std::string_view _version);
    static void set_runtime(std::string_view _runtime);
    static void set_runtime_id(std::string_view _runtime_id);
    static void set_process_id();
    static void set_runtime_version(std::string_view _runtime_version);
    static void set_profiler_version(std::string_view _profiler_version);
    static void set_url(std::string_view _url);
    static void set_tag(std::string_view _key, std::string_view _val);
    static void set_process_tags(std::string_view p_tags);
    static void set_output_filename(std::string_view _output_filename);
    static void set_max_timeout_ms(uint64_t _max_timeout_ms);

    static std::variant<Uploader, std::string> build();
};

} // namespace Datadog
