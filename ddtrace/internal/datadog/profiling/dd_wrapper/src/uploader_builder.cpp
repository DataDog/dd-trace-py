#include "uploader_builder.hpp"
#include "libdatadog_helpers.hpp"
#include "types.hpp"

#include <algorithm>
#include <mutex>
#include <string_view>

using namespace Datadog;

UploaderBuilder&
UploaderBuilder::set_env(std::string_view _env)
{
    std::lock_guard<std::mutex> lock(mtx);
    if (!_env.empty())
        env = _env;
    return *this;
}
UploaderBuilder&
UploaderBuilder::set_service(std::string_view _service)
{
    std::lock_guard<std::mutex> lock(mtx);
    if (!_service.empty())
        service = _service;
    return *this;
}
UploaderBuilder&
UploaderBuilder::set_version(std::string_view _version)
{
    std::lock_guard<std::mutex> lock(mtx);
    if (!_version.empty())
        version = _version;
    return *this;
}
UploaderBuilder&
UploaderBuilder::set_runtime(std::string_view _runtime)
{
    std::lock_guard<std::mutex> lock(mtx);
    if (!_runtime.empty())
        this->runtime = _runtime;
    return *this;
}
UploaderBuilder&
UploaderBuilder::set_runtime_version(std::string_view _runtime_version)
{
    std::lock_guard<std::mutex> lock(mtx);
    if (!_runtime_version.empty())
        runtime_version = _runtime_version;
    return *this;
}
UploaderBuilder&
UploaderBuilder::set_profiler_version(std::string_view _profiler_version)
{
    std::lock_guard<std::mutex> lock(mtx);
    if (!_profiler_version.empty())
        profiler_version = _profiler_version;
    return *this;
}
UploaderBuilder&
UploaderBuilder::set_url(std::string_view _url)
{
    std::lock_guard<std::mutex> lock(mtx);
    if (!_url.empty())
        url = _url;
    return *this;
}
UploaderBuilder&
UploaderBuilder::set_tag(std::string_view _key, std::string_view _val)
{
    std::lock_guard<std::mutex> lock(mtx);
    if (!_key.empty() && !_val.empty())
        user_tags[_key] = _val;
    return *this;
}

UploaderBuilder&
UploaderBuilder::set_runtime_id(std::string_view _runtime_id)
{
    std::lock_guard<std::mutex> lock(mtx);
    if (!_runtime_id.empty())
        runtime_id = _runtime_id;
    return *this;
}

std::unique_ptr<Uploader>
UploaderBuilder::build_ptr()
{
    // Setup the ddog_Exporter
    ddog_Vec_Tag tags = ddog_Vec_Tag_new();

    // Add the tags, emitting the first failure
    if (!add_tag(tags, ExportTagKey::env, env, errmsg) || !add_tag(tags, ExportTagKey::service, service, errmsg) ||
        !add_tag(tags, ExportTagKey::version, version, errmsg) ||
        !add_tag(tags, ExportTagKey::language, language, errmsg) ||
        !add_tag(tags, ExportTagKey::runtime, runtime, errmsg) ||
        !add_tag(tags, ExportTagKey::runtime_version, runtime_version, errmsg) ||
        !add_tag(tags, ExportTagKey::profiler_version, profiler_version, errmsg)) {
        ddog_Vec_Tag_drop(tags);
        return nullptr;
    }

    // Add the unsafe tags, if any
    if (std::any_of(user_tags.begin(), user_tags.end(), [&](const auto& kv) {
            return !add_tag_unsafe(tags, kv.first, kv.second, errmsg);
        })) {
        ddog_Vec_Tag_drop(tags);
        return nullptr;
    }

    ddog_prof_Exporter_NewResult res = ddog_prof_Exporter_new(
      to_slice("dd-trace-py"), to_slice(profiler_version), to_slice(family), &tags, ddog_Endpoint_agent(to_slice(url)));
    ddog_Vec_Tag_drop(tags);

    ddog_prof_Exporter* ddog_exporter = nullptr;
    if (res.tag == DDOG_PROF_EXPORTER_NEW_RESULT_OK) {
        ddog_exporter = res.ok;
    } else {
        errmsg = err_to_msg(&res.err, "Error initializing exporter");
        ddog_Error_drop(&res.err);
        return nullptr;
    }

    return std::make_unique<Uploader>(url, ddog_exporter);
}
