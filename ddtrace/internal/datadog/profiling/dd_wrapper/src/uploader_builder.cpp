#include "uploader_builder.hpp"

#include "libdatadog_helpers.hpp"
#include "profiler_state.hpp"
#include "sample.hpp"

#include <exception>
#include <numeric>
#include <optional>
#include <string>
#include <string_view>
#include <unistd.h>
#include <utility>
#include <vector>

void
Datadog::UploaderBuilder::set_env(std::string_view _dd_env)
{
    if (!_dd_env.empty()) {
        ProfilerState::get().dd_env = _dd_env;
    }
}

void
Datadog::UploaderBuilder::set_service(std::string_view _service)
{
    if (!_service.empty()) {
        ProfilerState::get().service = _service;
    }
}

void
Datadog::UploaderBuilder::set_version(std::string_view _version)
{
    if (!_version.empty()) {
        ProfilerState::get().version = _version;
    }
}

void
Datadog::UploaderBuilder::set_runtime(std::string_view _runtime)
{
    if (!_runtime.empty()) {
        ProfilerState::get().runtime = _runtime;
    }
}

void
Datadog::UploaderBuilder::set_runtime_id(std::string_view _runtime_id)
{
    if (!_runtime_id.empty()) {
        ProfilerState::get().runtime_id = _runtime_id;
    }
}

void
Datadog::UploaderBuilder::set_process_id()
{
    auto pid = getpid();
    ProfilerState::get().process_id = std::to_string(pid);
}

void
Datadog::UploaderBuilder::set_runtime_version(std::string_view _runtime_version)
{
    if (!_runtime_version.empty()) {
        ProfilerState::get().runtime_version = _runtime_version;
    }
}

void
Datadog::UploaderBuilder::set_profiler_version(std::string_view _profiler_version)
{
    if (!_profiler_version.empty()) {
        ProfilerState::get().profiler_version = _profiler_version;
    }
}

void
Datadog::UploaderBuilder::set_url(std::string_view _url)
{
    if (!_url.empty()) {
        ProfilerState::get().url = _url;
    }
}

void
Datadog::UploaderBuilder::set_tag(std::string_view _key, std::string_view _val)
{
    if (!_key.empty() && !_val.empty()) {
        ProfilerState::get().user_tags[std::string(_key)] = std::string(_val);
    }
}

void
Datadog::UploaderBuilder::set_process_tags(std::string_view p_tags)
{
    if (!p_tags.empty()) {
        ProfilerState::get().process_tags = p_tags;
    }
}

void
Datadog::UploaderBuilder::set_output_filename(std::string_view _output_filename)
{
    if (!_output_filename.empty()) {
        ProfilerState::get().output_filename = _output_filename;
    }
}

void
Datadog::UploaderBuilder::set_max_timeout_ms(uint64_t _max_timeout_ms)
{
    ProfilerState::get().max_timeout_ms = _max_timeout_ms;
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
    auto& state = ProfilerState::get();

    rust::Vec<ddprof::Tag> tags;

    // Add the tags. In the average case, the user has a structural problem with
    // one of their tags, but it's really annoying to have to iteratively fix several
    // tags, so we'll just collect all the reasons and report them all at once.
    std::vector<std::string> reasons{};
    const std::vector<std::pair<ExportTagKey, std::string_view>> tag_data = {
        { ExportTagKey::dd_env, state.dd_env },
        { ExportTagKey::service, state.service },
        { ExportTagKey::version, state.version },
        { ExportTagKey::language, language },
        { ExportTagKey::runtime, state.runtime },
        { ExportTagKey::runtime_id, state.runtime_id },
        { ExportTagKey::runtime_version, state.runtime_version },
        { ExportTagKey::profiler_version, state.profiler_version },
        { ExportTagKey::process_id, state.process_id }
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
    for (const auto& tag : state.user_tags) {
        std::string errmsg;
        if (!add_tag(tags, tag.first, tag.second, errmsg)) {
            reasons.push_back(std::string(tag.first) + ": " + errmsg);
        }
    }

    if (!reasons.empty()) {
        return "Error initializing exporter, missing or bad configuration: " + join(reasons, ", ");
    }

    std::optional<rust::Box<ddprof::ProfileExporter>> ddog_exporter;
    try {
        ddog_exporter = ddprof::ProfileExporter::create_agent_exporter(
          rust::Str(g_library_name.data(), g_library_name.size()),
          rust::Str(state.profiler_version.data(), state.profiler_version.size()),
          rust::Str(family.data(), family.size()),
          std::move(tags),
          rust::Str(state.url.data(), state.url.size()),
          state.max_timeout_ms,
          false);
    } catch (const std::exception& err) {
        return std::string("Error initializing CXX exporter: ") + err.what();
    }

    // Perform profile encoding before creating the Uploader.
    // Also take the Profiler Stats and reset the one being written to.
    std::optional<rust::Box<ddprof::EncodedProfile>> encoded;
    Datadog::ProfilerStats stats;
    {
        // Only keep the lock for the duration of the encoding operation.
        auto borrowed = state.profile_state.borrow();

        // Swap the ProfilerStats (which replaces the one being written to with an empty state).
        // We do this first as we still want to reset ProfilerStats if the serialization fails.
        std::swap(stats, borrowed.stats());
        borrowed.stats().copy_fast_copy_metadata_from(stats);

        try {
            encoded = borrowed.serialize();
        } catch (const std::exception& err) {
            return std::string("Error serializing CXX profile: ") + err.what();
        }
    }

    return std::variant<Datadog::Uploader, std::string>{ std::in_place_type<Datadog::Uploader>,
                                                         state.output_filename,
                                                         std::move(*ddog_exporter),
                                                         std::move(*encoded),
                                                         stats,
                                                         state.process_tags };
}
