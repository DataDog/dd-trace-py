#pragma once

#include "uploader.hpp"

#include <mutex>
#include <string>
#include <string_view>
#include <unordered_map>
#include <variant>

namespace Datadog {

struct LoggingString
{
  public:
    std::string value;

    LoggingString() = default;
    LoggingString(std::string_view sv)
      : value(sv)
    {
    }

    ~LoggingString() { std::cout << "Destructor called for output_filename: " << value << std::endl; }

    // Implicit conversion so it behaves like std::string
    operator std::string&() { return value; }
    operator const std::string&() const { return value; }

    bool empty() const { return value.empty(); }

    // Optional: provide assignment operator
    LoggingString& operator=(std::string_view sv)
    {
        value = sv;
        return *this;
    }
};

class UploaderBuilder
{
    using ExporterTagset = std::unordered_map<std::string, std::string>;
    static inline std::mutex tag_mutex{};

    // Building parameters
    static inline std::string dd_env;
    static inline std::string service;
    static inline std::string version;
    static inline std::string runtime{ g_runtime_name };
    static inline std::string runtime_id;
    static inline std::string runtime_version;
    static inline std::string profiler_version;
    static inline std::string url{ "http://localhost:8126" };
    static inline ExporterTagset user_tags{};
    static inline LoggingString output_filename{ "" };

    static constexpr std::string_view language{ g_language_name };
    static constexpr std::string_view family{ g_language_name };

  public:
    static void set_env(std::string_view _dd_env);
    static void set_service(std::string_view _service);
    static void set_version(std::string_view _version);
    static void set_runtime(std::string_view _runtime);
    static void set_runtime_id(std::string_view _runtime_id);
    static void set_runtime_version(std::string_view _runtime_version);
    static void set_profiler_version(std::string_view _profiler_version);
    static void set_url(std::string_view _url);
    static void set_tag(std::string_view _key, std::string_view _val);
    static void set_output_filename(std::string_view _output_filename);
    static std::string_view get_output_filename();

    static std::variant<Uploader, std::string> build();

    static void postfork_child();
};

} // namespace Datadog
