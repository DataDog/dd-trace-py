#include "exporter_manager.hpp"

#include "code_provenance.hpp"
#include "constants.hpp"
#include "datadog/common.h"
#include "datadog/profiling.h"
#include "libdatadog_helpers.hpp"
#include "sample.hpp"

#include <cerrno>
#include <cstring>
#include <fstream>
#include <iostream>
#include <numeric>
#include <sstream>
#include <unistd.h>
#include <vector>

namespace Datadog {

// Configuration setters
void
ExporterManager::set_env(std::string_view env)
{
    if (env.empty()) {
        return;
    }
    dd_env = env;
}

void
ExporterManager::set_service(std::string_view svc)
{
    if (svc.empty()) {
        return;
    }
    service = svc;
}

void
ExporterManager::set_version(std::string_view ver)
{
    if (ver.empty()) {
        return;
    }
    version = ver;
}

void
ExporterManager::set_runtime(std::string_view rt)
{
    if (rt.empty()) {
        return;
    }
    runtime = rt;
}

void
ExporterManager::set_runtime_id(std::string_view rt_id)
{
    if (rt_id.empty()) {
        return;
    }
    runtime_id = rt_id;
}

void
ExporterManager::set_process_id()
{
    process_id = std::to_string(getpid());
}

void
ExporterManager::set_runtime_version(std::string_view rt_ver)
{
    if (rt_ver.empty()) {
        return;
    }
    runtime_version = rt_ver;
}

void
ExporterManager::set_profiler_version(std::string_view prof_ver)
{
    if (prof_ver.empty()) {
        return;
    }
    profiler_version = prof_ver;
}

void
ExporterManager::set_url(std::string_view u)
{
    if (u.empty()) {
        return;
    }
    url = u;
}

void
ExporterManager::set_tag(std::string_view key, std::string_view val)
{
    if (key.empty() || val.empty()) {
        return;
    }
    user_tags[std::string(key)] = std::string(val);
}

void
ExporterManager::set_output_filename(std::string_view filename)
{
    if (filename.empty()) {
        return;
    }
    output_filename = filename;
}

void
ExporterManager::set_max_timeout_ms(uint64_t timeout)
{
    max_timeout_ms = timeout;
}

void
ExporterManager::set_process_tags(std::string_view p_tags)
{
    if (p_tags.empty()) {
        return;
    }
    process_tags = p_tags;
}

bool
ExporterManager::is_initialized()
{
    return initialized;
}

std::optional<ddog_prof_ProfileExporter>
ExporterManager::create_exporter()
{
    ddog_Vec_Tag tags = ddog_Vec_Tag_new();

    // Add standard tags
    std::vector<std::string> reasons;
    const std::vector<std::pair<ExportTagKey, std::string_view>> tag_data = {
        { ExportTagKey::dd_env, dd_env },
        { ExportTagKey::service, service },
        { ExportTagKey::version, version },
        { ExportTagKey::language, g_language_name },
        { ExportTagKey::runtime, runtime },
        { ExportTagKey::runtime_id, runtime_id },
        { ExportTagKey::runtime_version, runtime_version },
        { ExportTagKey::profiler_version, profiler_version },
        { ExportTagKey::process_id, process_id }
    };

    for (const auto& [tag, data] : tag_data) {
        if (!data.empty()) {
            std::string errmsg;
            if (!add_tag(tags, tag, data, errmsg)) {
                reasons.push_back(std::string(to_string(tag)) + ": " + errmsg);
            }
        }
    }

    // Add user tags
    for (const auto& [key, val] : user_tags) {
        std::string errmsg;
        if (!add_tag(tags, key, val, errmsg)) {
            reasons.push_back(key + ": " + errmsg);
        }
    }

    if (!reasons.empty()) {
        ddog_Vec_Tag_drop(tags);
        std::string joined = std::accumulate(
          reasons.begin(), reasons.end(), std::string(), [](const std::string& a, const std::string& b) {
              return a.empty() ? b : a + ", " + b;
          });
        std::cerr << "Error creating exporter, bad tags: " << joined << std::endl;
        return std::nullopt;
    }

    ddog_prof_ProfileExporter_Result res =
      ddog_prof_Exporter_new(to_slice(g_library_name),
                             to_slice(profiler_version),
                             to_slice(g_language_name),
                             &tags,
                             ddog_prof_Endpoint_agent(to_slice(url), max_timeout_ms));
    ddog_Vec_Tag_drop(tags);

    if (res.tag == DDOG_PROF_PROFILE_EXPORTER_RESULT_ERR_HANDLE_PROFILE_EXPORTER) {
        std::string errmsg = err_to_msg(&res.err, "Error creating exporter");
        std::cerr << errmsg << std::endl;
        ddog_Error_drop(&res.err);
        return std::nullopt;
    }

    return res.ok;
}

bool
ExporterManager::init()
{
    const std::lock_guard<std::mutex> lock(manager_mutex);

    if (initialized) {
        return true;
    }

    // If output_filename is set, we don't need a manager (file export mode)
    if (!output_filename.empty()) {
        initialized = true;
        return true;
    }

    auto exporter_opt = create_exporter();
    if (!exporter_opt.has_value()) {
        return false;
    }

    auto exporter = exporter_opt.value();
    ddog_prof_Result_HandleExporterManager result = ddog_prof_ExporterManager_new(&exporter);

    if (result.tag != DDOG_PROF_RESULT_HANDLE_EXPORTER_MANAGER_OK_HANDLE_EXPORTER_MANAGER) {
        std::string errmsg = err_to_msg(&result.err, "Error creating ExporterManager");
        std::cerr << errmsg << std::endl;
        ddog_Error_drop(&result.err);
        ddog_prof_Exporter_drop(&exporter);
        return false;
    }

    manager = result.ok;
    initialized = true;
    return true;
}

bool
ExporterManager::export_to_file(ddog_prof_EncodedProfile& encoded,
                                std::string_view internal_metadata_json,
                                uint64_t upload_seq)
{
    std::ostringstream oss;
    oss << output_filename << "." << getpid() << "." << upload_seq;
    const std::string base_filename = oss.str();
    const std::string pprof_filename = base_filename + ".pprof";

    std::ofstream out(pprof_filename, std::ios::binary);
    if (!out.is_open()) {
        std::cerr << "Error opening output file " << pprof_filename << ": " << strerror(errno) << std::endl;
        return false;
    }

    auto bytes_res = ddog_prof_EncodedProfile_bytes(&encoded);
    if (bytes_res.tag == DDOG_PROF_RESULT_BYTE_SLICE_ERR_BYTE_SLICE) {
        std::cerr << "Error getting bytes from encoded profile: " << err_to_msg(&bytes_res.err, "Error getting bytes")
                  << std::endl;
        ddog_Error_drop(&bytes_res.err);
        return false;
    }

    out.write(reinterpret_cast<const char*>(bytes_res.ok.ptr), bytes_res.ok.len);
    if (out.fail()) {
        std::cerr << "Error writing to output file " << pprof_filename << ": " << strerror(errno) << std::endl;
        return false;
    }

    const std::string internal_metadata_filename = base_filename + ".internal_metadata.json";
    std::ofstream out_internal_metadata(internal_metadata_filename);
    out_internal_metadata << internal_metadata_json;
    if (out_internal_metadata.fail()) {
        std::cerr << "Error writing metadata file " << internal_metadata_filename << ": " << strerror(errno)
                  << std::endl;
        return false;
    }

    return true;
}

bool
ExporterManager::upload()
{
    static bool already_warned = false;
    static uint64_t upload_seq = 0;

    const std::lock_guard<std::mutex> lock(manager_mutex);

    if (!initialized) {
        if (!already_warned) {
            already_warned = true;
            std::cerr << "ExporterManager::upload() called before init()" << std::endl;
        }
        return false;
    }

    // Serialize the profile and get stats
    ddog_prof_Profile_SerializeResult encoded;
    ProfilerStats stats;
    {
        auto borrowed = Sample::profile_borrow();
        std::swap(stats, borrowed.stats());

        encoded = ddog_prof_Profile_serialize(&borrowed.profile(), nullptr, nullptr);
        if (encoded.tag != DDOG_PROF_PROFILE_SERIALIZE_RESULT_OK) {
            std::string errmsg = err_to_msg(&encoded.err, "Error serializing profile");
            std::cerr << errmsg << std::endl;
            ddog_Error_drop(&encoded.err);
            return false;
        }
    }

    upload_seq++;
    auto internal_metadata_json = stats.get_internal_metadata_json();

    // File export mode
    if (!output_filename.empty()) {
        bool result = export_to_file(encoded.ok, internal_metadata_json, upload_seq);
        ddog_prof_EncodedProfile_drop(&encoded.ok);
        return result;
    }

    // Network upload mode - queue to ExporterManager
    std::vector<ddog_prof_Exporter_File> to_compress_files;
    std::string_view json_str = CodeProvenance::get_instance().get_json_str();

    if (!json_str.empty()) {
        to_compress_files.push_back({
          .name = to_slice("code-provenance.json"),
          .file = to_byte_slice(json_str),
        });
    }

    auto internal_metadata_json_slice = to_slice(internal_metadata_json);

    ddog_CharSlice process_tags_slice;
    const ddog_CharSlice* optional_process_tags_ptr = nullptr;
    if (!process_tags.empty()) {
        process_tags_slice = to_slice(process_tags);
        optional_process_tags_ptr = &process_tags_slice;
    }

    ddog_prof_Exporter_Slice_File files_to_compress = {
        .ptr = to_compress_files.data(),
        .len = to_compress_files.size(),
    };

    ddog_VoidResult queue_result = ddog_prof_ExporterManager_queue(&manager,
                                                                   &encoded.ok,
                                                                   files_to_compress,
                                                                   nullptr, // optional_additional_tags
                                                                   optional_process_tags_ptr,
                                                                   &internal_metadata_json_slice,
                                                                   nullptr); // optional_info_json

    // Note: ddog_prof_ExporterManager_queue takes ownership of encoded.ok, so we don't drop it

    if (queue_result.tag != DDOG_VOID_RESULT_OK) {
        std::string errmsg = err_to_msg(&queue_result.err, "Error queueing profile for upload");
        std::cerr << errmsg << std::endl;
        ddog_Error_drop(&queue_result.err);
        return false;
    }

    return true;
}

void
ExporterManager::prefork()
{
    // Lock to prevent concurrent uploads during fork
    manager_mutex.lock();

    if (!initialized || manager.inner == nullptr) {
        // No manager to suspend, but keep lock held for postfork
        return;
    }

    ddog_VoidResult result = ddog_prof_ExporterManager_prefork(&manager);
    if (result.tag != DDOG_VOID_RESULT_OK) {
        // We failed to prefork the manager, we can't try to use it in the future
        initialized = false;

        // Unlock the mutex to avoid deadlocks
        manager_mutex.unlock();

        std::string errmsg = err_to_msg(&result.err, "Error in ExporterManager prefork");
        std::cerr << errmsg << std::endl;
        ddog_Error_drop(&result.err);
    }
}

void
ExporterManager::postfork_parent()
{
    if (initialized && manager.inner != nullptr) {
        ddog_VoidResult result = ddog_prof_ExporterManager_postfork_parent(&manager);
        if (result.tag != DDOG_VOID_RESULT_OK) {
            // We failed to postfork the parent, we can't try to use the manager in the future
            initialized = false;
            ddog_prof_ExporterManager_drop(&manager);

            std::string errmsg = err_to_msg(&result.err, "Error in ExporterManager postfork_parent");
            std::cerr << errmsg << std::endl;
            ddog_Error_drop(&result.err);
        }
    }

    manager_mutex.unlock();
}

void
ExporterManager::postfork_child()
{
    // Reinitialize mutex in child (copying mutex across fork is UB)
    new (&manager_mutex) std::mutex();

    if (initialized && manager.inner != nullptr) {
        ddog_VoidResult result = ddog_prof_ExporterManager_postfork_child(&manager);
        if (result.tag != DDOG_VOID_RESULT_OK) {
            // We failed to postfork the child, we can't try to use the manager in the future
            initialized = false;
            ddog_prof_ExporterManager_drop(&manager);

            std::string errmsg = err_to_msg(&result.err, "Error in ExporterManager postfork_child");
            std::cerr << errmsg << std::endl;
            ddog_Error_drop(&result.err);
        }
    }
}

void
ExporterManager::shutdown()
{
    const std::lock_guard<std::mutex> lock(manager_mutex);

    if (!initialized) {
        return;
    }

    ddog_VoidResult result = ddog_prof_ExporterManager_abort(&manager);
    if (result.tag != DDOG_VOID_RESULT_OK) {
        std::string errmsg = err_to_msg(&result.err, "Error in ExporterManager shutdown");
        std::cerr << errmsg << std::endl;
        ddog_Error_drop(&result.err);
    }

    // Drop the manager if it was created (network mode)
    if (manager.inner != nullptr) {
        ddog_prof_ExporterManager_drop(&manager);
        manager.inner = nullptr;
    }

    initialized = false;
}

} // namespace Datadog
