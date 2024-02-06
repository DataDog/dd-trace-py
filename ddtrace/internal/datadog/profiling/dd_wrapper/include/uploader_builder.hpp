#pragma once

#include "uploader.hpp"

#include <mutex>
#include <string>
#include <string_view>
#include <unordered_map>

namespace Datadog {

class UploaderBuilder
{
    using ExporterTagset = std::unordered_map<std::string_view, std::string_view>;

    // Mutex
    std::mutex mtx;

    // Internal/queryable state
    std::string errmsg;

    // Building parameters
    // TODO Are these the right defaults?
    std::string env = "";
    std::string service = "";
    std::string version = "";
    std::string runtime = "cython";
    std::string runtime_version = "";
    std::string runtime_id = "";
    std::string profiler_version = "";  // TODO get this at build time
    std::string url = "http://localhost:8126";
    ExporterTagset user_tags;

    static constexpr std::string_view language = "python";
    static constexpr std::string_view family = "python";

  public:
    UploaderBuilder& set_env(std::string_view _env);
    UploaderBuilder& set_service(std::string_view _service);
    UploaderBuilder& set_version(std::string_view _version);
    UploaderBuilder& set_runtime(std::string_view _runtime);
    UploaderBuilder& set_runtime_version(std::string_view _runtime_version);
    UploaderBuilder& set_runtime_id(std::string_view _runtime_id);
    UploaderBuilder& set_profiler_version(std::string_view _profiler_version);
    UploaderBuilder& set_url(std::string_view _url);
    UploaderBuilder& set_tag(std::string_view _key, std::string_view _val);

    std::unique_ptr<Uploader> build_ptr();
};

} // namespace Datadog
