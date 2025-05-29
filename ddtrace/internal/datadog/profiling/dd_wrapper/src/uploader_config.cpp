#include "uploader_config.hpp"

#include <string_view>

namespace Datadog {

void
UploaderConfig::set_env(std::string_view _dd_env)
{
    if (!_dd_env.empty()) {
        dd_env = _dd_env;
    }
}

void
UploaderConfig::set_service(std::string_view _service)
{
    if (!_service.empty()) {
        service = _service;
    }
}

void
UploaderConfig::set_version(std::string_view _version)
{
    if (!_version.empty()) {
        version = _version;
    }
}

void
UploaderConfig::set_runtime(std::string_view _runtime)
{
    if (!_runtime.empty()) {
        runtime = _runtime;
    }
}

void
UploaderConfig::set_runtime_id(std::string_view _runtime_id)
{
    if (!_runtime_id.empty()) {
        runtime_id = _runtime_id;
    }
}

void
UploaderConfig::set_runtime_version(std::string_view _runtime_version)
{
    if (!_runtime_version.empty()) {
        runtime_version = _runtime_version;
    }
}

void
UploaderConfig::set_profiler_version(std::string_view _profiler_version)
{
    if (!_profiler_version.empty()) {
        profiler_version = _profiler_version;
    }
}

void
UploaderConfig::set_url(std::string_view _url)
{
    if (!_url.empty()) {
        url = _url;
    }
}

void
UploaderConfig::set_tag(std::string_view _key, std::string_view _val)
{

    if (!_key.empty() && !_val.empty()) {
        user_tags[std::string(_key)] = std::string(_val);
    }
}

std::string_view
UploaderConfig::get_env() const
{
    return dd_env;
}

std::string_view
UploaderConfig::get_service() const
{
    return service;
}

std::string_view
UploaderConfig::get_version() const
{
    return version;
}

std::string_view
UploaderConfig::get_runtime() const
{
    return runtime;
}

std::string_view
UploaderConfig::get_runtime_id() const
{
    return runtime_id;
}

std::string_view
UploaderConfig::get_runtime_version() const
{
    return runtime_version;
}

std::string_view
UploaderConfig::get_profiler_version() const
{
    return profiler_version;
}

std::string_view
UploaderConfig::get_url() const
{
    return url;
}

std::string_view
UploaderConfig::get_language() const
{
    return language;
}

std::string_view
UploaderConfig::get_family() const
{
    return family;
}

const UploaderConfig::ExporterTagset&
UploaderConfig::get_user_tags() const
{
    return user_tags;
}

}
