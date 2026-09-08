#include "uploader.hpp"

#include "profiler_state.hpp"

#include <fstream> // ofstream
#include <iostream>
#include <sstream>  // ostringstream
#include <unistd.h> // getpid

using namespace Datadog;

Datadog::Uploader::Uploader(std::string_view _output_filename,
                            std::vector<std::uint8_t> _encoded_profile,
                            Datadog::ProfilerStats _stats,
                            std::string_view _process_tags)
  : output_filename{ _output_filename }
  , encoded_profile{ std::move(_encoded_profile) }
  , profiler_stats{ _stats }
  , process_tags{ _process_tags }
{
    // Increment the upload sequence number every time we build an uploader.
    // Uploaders are use-once-and-destroy.
    ProfilerState::get().upload_seq++;
}

Datadog::Uploader::~Uploader() = default;

bool
Datadog::Uploader::export_to_file(const std::vector<std::uint8_t>& encoded, std::string_view internal_metadata_json)
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

    out.write(reinterpret_cast<const char*>(encoded.data()), static_cast<std::streamsize>(encoded.size()));
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
    if (!output_filename.empty()) {
        return export_to_file(encoded_profile, profiler_stats.get_internal_metadata_json());
    }

    std::cerr << "CXX R&D profile path currently supports output_filename/file export only. "
                 "Agent/agentless upload requires a CXX encoded-profile split."
              << std::endl;
    return false;
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
    // No-op for the Step 1 CXX R&D path. Agent/agentless upload still needs a
    // CXX encoded-profile split before cancellation can be restored.
}
