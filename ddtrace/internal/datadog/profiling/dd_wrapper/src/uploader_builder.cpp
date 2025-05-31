#include "uploader_builder.hpp"

#include "libdatadog_helpers.hpp"
#include "uploader_config.hpp"

#include <numeric>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace Datadog {

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

std::variant<Uploader, std::string>
UploaderBuilder::build(std::unique_ptr<UploaderConfig> config)
{
    // Setup the ddog_Exporter
    ddog_Vec_Tag tags = ddog_Vec_Tag_new();

    // Add the tags.  In the average case, the user has a structural problem with
    // one of their tags, but it's really annoying to have to iteratively fix several
    // tags, so we'll just collect all the reasons and report them all at once.
    std::vector<std::string> reasons{};
    const std::vector<std::pair<ExportTagKey, std::string_view>> tag_data = {
        { ExportTagKey::dd_env, config->get_env() },
        { ExportTagKey::service, config->get_service() },
        { ExportTagKey::version, config->get_version() },
        { ExportTagKey::language, config->get_language() },
        { ExportTagKey::runtime, config->get_runtime() },
        { ExportTagKey::runtime_id, config->get_runtime_id() },
        { ExportTagKey::runtime_version, config->get_runtime_version() },
        { ExportTagKey::profiler_version, config->get_profiler_version() },
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
    for (const auto& tag : config->get_user_tags()) {
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
                             to_slice(config->get_profiler_version()),
                             to_slice(config->get_family()),
                             &tags,
                             ddog_prof_Endpoint_agent(to_slice(config->get_url())));
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
    return std::variant<Uploader, std::string>{ std::in_place_type<Uploader>,
                                                config->get_output_filename(),
                                                *ddog_exporter };
}

}
