#include "uploader_builder.hpp"

#include "libdatadog_helpers.hpp"

#include <mutex>
#include <numeric>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

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
        const std::lock_guard<std::mutex> lock(tag_mutex);
        user_tags[std::string(_key)] = std::string(_val);
    }
}

void
UploaderConfig::set_output_filename(std::string_view _output_filename)
{
    if (!_output_filename.empty()) {
        output_filename = _output_filename;
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
UploaderConfig::get_output_filename() const
{
    return output_filename;
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

void
UploaderConfig::postfork_child()
{
    new (&UploaderConfig::get_instance()->tag_mutex) std::mutex();
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
        { ExportTagKey::dd_env, UploaderConfig::get_instance()->get_env() },
        { ExportTagKey::service, UploaderConfig::get_instance()->get_service() },
        { ExportTagKey::version, UploaderConfig::get_instance()->get_version() },
        { ExportTagKey::language, UploaderConfig::get_instance()->get_language() },
        { ExportTagKey::runtime, UploaderConfig::get_instance()->get_runtime() },
        { ExportTagKey::runtime_id, UploaderConfig::get_instance()->get_runtime_id() },
        { ExportTagKey::runtime_version, UploaderConfig::get_instance()->get_runtime_version() },
        { ExportTagKey::profiler_version, UploaderConfig::get_instance()->get_profiler_version() },
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
    for (const auto& tag : UploaderConfig::get_instance()->get_user_tags()) {
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
    ddog_prof_ProfileExporter_Result res =
      ddog_prof_Exporter_new(to_slice("dd-trace-py"),
                             to_slice(UploaderConfig::get_instance()->get_profiler_version()),
                             to_slice(UploaderConfig::get_instance()->get_family()),
                             &tags,
                             ddog_prof_Endpoint_agent(to_slice(UploaderConfig::get_instance()->get_url())));
    ddog_Vec_Tag_drop(tags);

    if (res.tag == DDOG_PROF_PROFILE_EXPORTER_RESULT_ERR_HANDLE_PROFILE_EXPORTER) {
        auto& err = res.err;
        std::string errmsg = Datadog::err_to_msg(&err, "Error initializing exporter");
        ddog_Error_drop(&err); // errmsg contains a copy of err.message
        return errmsg;
    }

    auto ddog_exporter = &res.ok;

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

    // We create a std::variant here instead of creating a temporary Uploader object.
    // i.e. return Datadog::Uploader{ output_filename, *ddog_exporter }
    // because above code creates a temporary Uploader object, moves it into the
    // variant, and then the destructor of the temporary Uploader object is called
    // when the temporary Uploader object goes out of scope.
    // This was necessary to avoid double-free from calling ddog_prof_Exporter_drop()
    // in the destructor of Uploader. See comments in uploader.hpp for more details.
    return std::variant<Datadog::Uploader, std::string>{ std::in_place_type<Datadog::Uploader>,
                                                         UploaderConfig::get_instance()->get_output_filename(),
                                                         *ddog_exporter };
}

}
