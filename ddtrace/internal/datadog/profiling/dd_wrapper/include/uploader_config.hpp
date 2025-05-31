#pragma once

#include "constants.hpp"

#include <string>
#include <string_view>
#include <unordered_map>

namespace Datadog {
class UploaderConfig
{
    using ExporterTagset = std::unordered_map<std::string, std::string>;

  private:
    std::string dd_env;
    std::string service;
    std::string version;
    std::string runtime;
    std::string runtime_id;
    std::string runtime_version;
    std::string profiler_version;
    std::string url;

    ExporterTagset user_tags;
    std::string output_filename;

    std::string language{ g_language_name };
    std::string family{ g_language_name };

  public:
    UploaderConfig() = default;
    ~UploaderConfig() = default;

    void set_env(std::string_view _dd_env);
    void set_service(std::string_view _service);
    void set_version(std::string_view _version);
    void set_runtime(std::string_view _runtime);
    void set_runtime_id(std::string_view _runtime_id);
    void set_runtime_version(std::string_view _runtime_version);
    void set_profiler_version(std::string_view _profiler_version);
    void set_url(std::string_view _url);
    void set_tag(std::string_view _key, std::string_view _val);
    void set_output_filename(std::string_view _output_filename);

    std::string_view get_env() const;
    std::string_view get_service() const;
    std::string_view get_version() const;
    std::string_view get_runtime() const;
    std::string_view get_runtime_id() const;
    std::string_view get_runtime_version() const;
    std::string_view get_profiler_version() const;
    std::string_view get_url() const;
    std::string_view get_output_filename() const;
    std::string_view get_language() const;
    std::string_view get_family() const;
    const ExporterTagset& get_user_tags() const;
};
}
