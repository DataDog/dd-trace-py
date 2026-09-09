#include "uploader.hpp"

#include "code_provenance.hpp"
#include "profiler_state.hpp"

#include <exception>
#include <fstream> // ofstream
#include <iostream>
#include <sstream>  // ostringstream
#include <unistd.h> // getpid

using namespace Datadog;

Datadog::Uploader::Uploader(std::string_view _output_filename,
                            rust::Box<ddprof::ProfileExporter> _ddog_exporter,
                            rust::Box<ddprof::EncodedProfile> _encoded_profile,
                            Datadog::ProfilerStats _stats,
                            std::string_view _process_tags)
  : output_filename{ _output_filename }
  , ddog_exporter{ std::move(_ddog_exporter) }
  , encoded_profile{ std::move(_encoded_profile) }
  , profiler_stats{ _stats }
  , process_tags{ _process_tags }
{
    // Increment the upload sequence number every time we build an uploader.
    // Uploaders are use-once-and-destroy.
    ProfilerState::get().upload_seq++;
}

Datadog::Uploader::~Uploader()
{
    if (owns_upload_state) {
        cancel_inflight();
    }
}

bool
Datadog::Uploader::export_to_file(const ddprof::EncodedProfile& encoded, std::string_view internal_metadata_json)
{
    // Write the profile to a file using the following format for filename:
    // <output_filename>.<process_id>.<sequence_number>
    std::ostringstream oss;
    oss << output_filename << "." << getpid() << "." << ProfilerState::get().upload_seq.load();
    const std::string base_filename = oss.str();
    const std::string pprof_filename = base_filename + ".pprof";

    std::ofstream out(pprof_filename, std::ios::binary);
    if (!out.is_open()) {
        std::cerr << "Error opening output file " << pprof_filename << std::endl;
        return false;
    }

    const auto bytes = encoded.bytes();
    out.write(reinterpret_cast<const char*>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
    if (out.fail()) {
        std::cerr << "Error writing to output file " << pprof_filename << std::endl;
        return false;
    }

    const std::string internal_metadata_filename = base_filename + ".internal_metadata.json";
    std::ofstream out_internal_metadata(internal_metadata_filename);
    out_internal_metadata << internal_metadata_json;
    if (out_internal_metadata.fail()) {
        std::cerr << "Error writing to internal metadata file " << internal_metadata_filename << std::endl;
        return false;
    }

    const auto& info_json = ProfilerState::get().profiler_settings_info_json;
    if (!info_json.empty()) {
        const std::string info_filename = base_filename + ".info.json";
        std::ofstream out_info(info_filename);
        out_info << info_json;
        if (out_info.fail()) {
            std::cerr << "Error writing to info file " << info_filename << std::endl;
            return false;
        }
    }

    return true;
}

bool
Datadog::Uploader::upload_unlocked()
{
    if (!encoded_profile.has_value()) {
        std::cerr << "No encoded profile available for upload" << std::endl;
        return false;
    }

    const auto internal_metadata_json = profiler_stats.get_internal_metadata_json();

    if (!output_filename.empty()) {
        return export_to_file(**encoded_profile, internal_metadata_json);
    }

    if (!ddog_exporter.has_value()) {
        std::cerr << "No profile exporter available for upload" << std::endl;
        return false;
    }

    rust::Vec<ddprof::AttachmentFile> files_to_compress;
    const std::string_view code_provenance_json = CodeProvenance::get_instance().get_json_str();
    if (!code_provenance_json.empty()) {
        files_to_compress.reserve(1);
        files_to_compress.push_back(ddprof::AttachmentFile{
          rust::Str("code-provenance.json"),
          { reinterpret_cast<const std::uint8_t*>(code_provenance_json.data()), code_provenance_json.size() },
        });
    }

    rust::Vec<ddprof::Tag> additional_tags;
    const auto& info_json = ProfilerState::get().profiler_settings_info_json;

    try {
        auto new_cancel = ddprof::new_cancellation_token();
        auto new_cancel_clone_for_request = new_cancel->clone_token();
        auto& state = ProfilerState::get();
        {
            const std::lock_guard<std::mutex> cancel_lock(state.upload_cancel_mtx);
            if (state.upload_cancel.has_value()) {
                (*state.upload_cancel)->cancel();
                state.upload_cancel.reset();
            }
            state.upload_cancel = std::move(new_cancel);
        }

        auto encoded = std::move(*encoded_profile);
        encoded_profile.reset();

        (*ddog_exporter)
          ->send_encoded_profile_with_cancellation(
            std::move(encoded),
            std::move(files_to_compress),
            std::move(additional_tags),
            rust::Str(process_tags.data(), process_tags.size()),
            rust::Str(internal_metadata_json.data(), internal_metadata_json.size()),
            rust::Str(info_json.data(), info_json.size()),
            *new_cancel_clone_for_request);
        ddog_exporter.reset();
        return true;
    } catch (const std::exception& err) {
        errmsg = std::string("Error uploading CXX profile: ") + err.what();
        std::cerr << errmsg << std::endl;
        ddog_exporter.reset();
        return false;
    }
}

bool
Datadog::Uploader::upload()
{
    // The upload operation sets up some global state in libdatadog (the tokio runtime), so
    // we ensure exclusivity here.
    const std::lock_guard<std::mutex> lock_guard(ProfilerState::get().upload_lock);
    return upload_unlocked();
}

void
Datadog::Uploader::lock()
{
    ProfilerState::get().upload_lock.lock();
}

void
Datadog::Uploader::unlock()
{
    ProfilerState::get().upload_lock.unlock();
}

void
Datadog::Uploader::cancel_inflight()
{
    auto& state = ProfilerState::get();
    const std::lock_guard<std::mutex> cancel_lock(state.upload_cancel_mtx);
    if (state.upload_cancel.has_value()) {
        (*state.upload_cancel)->cancel();
        state.upload_cancel.reset();
    }
}
