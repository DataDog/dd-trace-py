#include "uploader_builder.hpp"

#include "libdatadog_helpers.hpp"
#include "profiler_state.hpp"
#include "sample.hpp"

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

namespace {
using namespace Datadog;

// Create the exporter if it does not already exist.
// Caller must hold ProfilerState::upload_lock.
//
// Only the static identity tags (env, service, version, language, runtime,
// runtime_id, runtime_version, profiler_version, process_id) are baked into
// the exporter here. User-defined tags are passed per-send via
// optional_additional_tags so they can change without rebuilding the exporter.
std::optional<std::string>
ensure_exporter(ProfilerState& state)
{
    if (state.exporter.inner != nullptr) {
        return std::nullopt;
    }

    ddog_Vec_Tag tags = ddog_Vec_Tag_new();

    // Collect all tag errors so the user can fix them in one pass.
    std::vector<std::string> reasons{};
    const std::vector<std::pair<ExportTagKey, std::string_view>> tag_data = {
        { ExportTagKey::dd_env, state.dd_env },
        { ExportTagKey::service, state.service },
        { ExportTagKey::version, state.version },
        { ExportTagKey::language, g_language_name },
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

    if (!reasons.empty()) {
        ddog_Vec_Tag_drop(tags);
        return "Error initializing exporter, missing or bad configuration: " + join(reasons, ", ");
    }

    ddog_prof_ProfileExporter_Result res = ddog_prof_Exporter_new(
      to_slice("dd-trace-py"),
      to_slice(state.profiler_version),
      to_slice(g_language_name),
      &tags,
      ddog_prof_Endpoint_agent(to_slice(state.url), state.max_timeout_ms, /*use_system_resolver=*/false));
    ddog_Vec_Tag_drop(tags);

    if (res.tag == DDOG_PROF_PROFILE_EXPORTER_RESULT_ERR_HANDLE_PROFILE_EXPORTER) {
        auto& err = res.err;
        std::string errmsg = err_to_msg(&err, "Error initializing exporter");
        ddog_Error_drop(&err);
        return errmsg;
    }

    state.exporter = res.ok;
    return std::nullopt;
}

} // namespace

ddog_Vec_Tag
Datadog::UploaderBuilder::build_user_tag_vec(std::vector<std::string>& reasons)
{
    auto& state = ProfilerState::get();
    ddog_Vec_Tag tags = ddog_Vec_Tag_new();
    for (const auto& tag : state.user_tags) {
        std::string errmsg;
        if (!add_tag(tags, tag.first, tag.second, errmsg)) {
            reasons.push_back(std::string(tag.first) + ": " + errmsg);
        }
    }
    return tags;
}

std::variant<Datadog::Uploader, std::string>
Datadog::UploaderBuilder::build()
{
    auto& state = ProfilerState::get();

    // Lazily create the exporter on first build. Subsequent builds reuse it.
    // The caller (ddup_upload) holds upload_lock, so this is serialized with
    // prefork / cleanup — the only places that drop the exporter.
    if (auto err = ensure_exporter(state)) {
        return std::move(*err);
    }

    // Perform profile encoding before creating the Uploader
    // Also take the Profiler Stats and reset the one being written to
    ddog_prof_Profile_SerializeResult encoded;
    Datadog::ProfilerStats stats;
    {
        // Only keep the lock for the duration of the encoding operation.
        auto borrowed = state.profile_state.borrow();

        // Swap the ProfilerStats (which replaces the one being written to with an empty state).
        // We do this first as we still want to reset ProfilerStats if the serialization fails.
        std::swap(stats, borrowed.stats());

        // Try to encode the Profile (which will also reset it)
        encoded = ddog_prof_Profile_serialize(&borrowed.profile(), nullptr, nullptr);
        if (encoded.tag != DDOG_PROF_PROFILE_SERIALIZE_RESULT_OK) {
            auto err = encoded.err;
            std::string errmsg = Datadog::err_to_msg(&err, "Error serializing profile");
            ddog_Error_drop(&err);
            // exporter is intentionally kept; encoding failure is not the exporter's fault.
            return errmsg;
        }
    }

    return std::variant<Datadog::Uploader, std::string>{
        std::in_place_type<Datadog::Uploader>, state.output_filename, encoded.ok, stats, state.process_tags
    };
}
