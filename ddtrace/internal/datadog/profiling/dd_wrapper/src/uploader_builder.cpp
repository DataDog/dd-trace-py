#include "uploader_builder.hpp"
#include "libdatadog_helpers.hpp"

#include <mutex>
#include <numeric>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

void
Datadog::UploaderBuilder::set_env(std::string_view _dd_env)
{
    if (!_dd_env.empty()) {
        dd_env = _dd_env;
    }
}
void
Datadog::UploaderBuilder::set_service(std::string_view _service)
{
    if (!_service.empty()) {
        service = _service;
    }
}
void
Datadog::UploaderBuilder::set_version(std::string_view _version)
{
    if (!_version.empty()) {
        version = _version;
    }
}
void
Datadog::UploaderBuilder::set_runtime(std::string_view _runtime)
{
    if (!_runtime.empty()) {
        runtime = _runtime;
    }
}
void
Datadog::UploaderBuilder::set_runtime_version(std::string_view _runtime_version)
{
    if (!_runtime_version.empty()) {
        runtime_version = _runtime_version;
    }
}
void
Datadog::UploaderBuilder::set_profiler_version(std::string_view _profiler_version)
{
    if (!_profiler_version.empty()) {
        profiler_version = _profiler_version;
    }
}
void
Datadog::UploaderBuilder::set_url(std::string_view _url)
{
    if (!_url.empty()) {
        url = _url;
    }
}
void
Datadog::UploaderBuilder::set_tag(std::string_view _key, std::string_view _val)
{

    if (!_key.empty() && !_val.empty()) {
        const std::lock_guard<std::mutex> lock(tag_mutex);
        user_tags[std::string(_key)] = std::string(_val);
    }
}

void
Datadog::UploaderBuilder::set_runtime_id(std::string_view _runtime_id)
{
    if (!_runtime_id.empty()) {
        runtime_id = _runtime_id;
    }
}

std::string
join(const std::vector<std::string>& vec, const std::string& delim)
{
    return std::accumulate(vec.begin(),
                           vec.end(),
                           std::string(),
                           [&delim](const std::string& left, const std::string& right) -> std::string {
                               // If the left and right operands are empty, we don't want to add a delimiter
                               if (left.empty()) {
                                   return right;
                               }
                               if (right.empty()) {
                                   return left;
                               }
                               return left + delim + right;
                           });
}

std::variant<Datadog::Uploader, std::string>
Datadog::UploaderBuilder::build()
{
    // Setup the ddog_Exporter
    ddog_Vec_Tag tags = ddog_Vec_Tag_new();

    // Add the tags.  In the average case, the user has a structural problem with
    // one of their tags, but it's really annoying to have to iteratively fix several
    // tags, so we'll just collect all the reasons and report them all at once.
    std::vector<std::string> reasons{};
    const std::vector<std::pair<ExportTagKey, std::string_view>> tag_data = {
        { ExportTagKey::dd_env, dd_env },
        { ExportTagKey::service, service },
        { ExportTagKey::version, version },
        { ExportTagKey::language, language },
        { ExportTagKey::runtime, runtime },
        { ExportTagKey::runtime_version, runtime_version },
        { ExportTagKey::profiler_version, profiler_version },
    };

    for (const auto& [tag, data] : tag_data) {
        if (!data.empty()) {
            std::string errmsg;
            if (!add_tag(tags, tag, data, errmsg)) {
                reasons.push_back(std::string(to_string(tag)) + ": " + errmsg);
            }
        }
    }

    // Add the user-defined tags, if any.
    for (const auto& tag : user_tags) {
        std::string errmsg;
        if (!add_tag(tags, tag.first, tag.second, errmsg)) {
            reasons.push_back(std::string(tag.first) + ": " + errmsg);
        }
    }

    if (!reasons.empty()) {
        ddog_Vec_Tag_drop(tags);
        return "Error initializing exporter, missing or bad configuration: " + join(reasons, ", ");
    }

    // If we're here, the tags are good, so we can initialize the exporter
    ddog_prof_Exporter_NewResult res = ddog_prof_Exporter_new(
      to_slice("dd-trace-py"), to_slice(profiler_version), to_slice(family), &tags, ddog_Endpoint_agent(to_slice(url)));
    ddog_Vec_Tag_drop(tags);

    auto ddog_exporter_result = Datadog::get_newexporter_result(res);
    ddog_prof_Exporter* ddog_exporter = nullptr;
    if (std::holds_alternative<ddog_prof_Exporter*>(ddog_exporter_result)) {
        ddog_exporter = std::get<ddog_prof_Exporter*>(ddog_exporter_result);
    } else {
        auto& err = std::get<ddog_Error>(ddog_exporter_result);
        std::string errmsg = Datadog::err_to_msg(&err, "Error initializing exporter");
        ddog_Error_drop(&err); // errmsg contains a copy of err.message
        return errmsg;
    }

    return Datadog::Uploader{ url, ddog_exporter };
}
