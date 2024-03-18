#pragma once

#include "uploader.hpp"

#include <mutex>
#include <string>
#include <string_view>
#include <unordered_map>
#include <variant>

namespace Datadog {

class UploaderBuilder
{
    using ExporterTagset = std::unordered_map<std::string_view, std::string_view>;
    static inline std::mutex tag_mutex{};

    // Building parameters
    // TODO Are these the right defaults?
    static inline std::string dd_env;
    static inline std::string service;
    static inline std::string version;
    static inline std::string runtime{ "cython" };
    static inline std::string runtime_version;
    static inline std::string runtime_id;
    static inline std::string profiler_version; // TODO: get this at build time
    static inline std::string url{ "http://localhost:8126" };
    static inline ExporterTagset user_tags{};

    static constexpr std::string_view language{ "python" };
    static constexpr std::string_view family{ "python" };

  public:
    static void set_env(std::string_view dd_env_);
    static void set_service(std::string_view _service);
    static void set_version(std::string_view _version);
    static void set_runtime(std::string_view _runtime);
    static void set_runtime_version(std::string_view _runtime_version);
    static void set_runtime_id(std::string_view _runtime_id);
    static void set_profiler_version(std::string_view _profiler_version);
    static void set_url(std::string_view _url);
    static void set_tag(std::string_view _key, std::string_view _val);

    static std::variant<Uploader, std::string> build();
};

} // namespace Datadog
