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
Datadog::UploaderBuilder::set_runtime_id(std::string_view _runtime_id)
{
    if (!_runtime_id.empty()) {
        runtime_id = _runtime_id;
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
Datadog::UploaderBuilder::set_output_filename(std::string_view _output_filename)
{
    if (!_output_filename.empty()) {
        output_filename = _output_filename;
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
        { ExportTagKey::runtime_id, runtime_id },
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
    ddog_prof_ProfileExporter_Result res = ddog_prof_Exporter_new(to_slice("dd-trace-py"),
                                                                  to_slice(profiler_version),
                                                                  to_slice(family),
                                                                  &tags,
                                                                  ddog_prof_Endpoint_agent(to_slice(url)));
    ddog_Vec_Tag_drop(tags);

    if (res.tag == DDOG_PROF_PROFILE_EXPORTER_RESULT_ERR_HANDLE_PROFILE_EXPORTER) {
        auto& err = res.err;
        std::string errmsg = Datadog::err_to_msg(&err, "Error initializing exporter");
        ddog_Error_drop(&err); // errmsg contains a copy of err.message
        return errmsg;
    }

    // libdatadog owns the inner pointer, but C++ owns ddog_prof_ProfileExporter
    auto ddog_exporter = new ddog_prof_ProfileExporter(res.ok);

    // 5s is a common timeout parameter for Datadog profilers
    const uint64_t max_timeout_ms = 5000;
    auto set_timeout_result = ddog_prof_Exporter_set_timeout(ddog_exporter, max_timeout_ms);
    if (set_timeout_result.tag == DDOG_VOID_RESULT_ERR) {
        auto& err = set_timeout_result.err;
        std::string errmsg = Datadog::err_to_msg(&err, "Error setting timeout on exporter");
        ddog_Error_drop(&err); // errmsg contains a copy of err.message
        // If set_timeout had failed, then the ddog_exporter must have been a
        // null pointer, so it's redundant to drop it here but it should also
        // be safe to do so.
        ddog_prof_Exporter_drop(ddog_exporter);
        return errmsg;
    }

    return Datadog::Uploader{ output_filename, ddog_exporter };
}

void
Datadog::UploaderBuilder::postfork_child()
{
    // NB placement-new to re-init and leak the mutex because doing anything else is UB
    new (&tag_mutex) std::mutex();
}
