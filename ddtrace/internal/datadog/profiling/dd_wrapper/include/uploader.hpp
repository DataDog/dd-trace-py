#pragma once

#include "profiler_stats.hpp"

#include <string>

extern "C"
{
#include "datadog/profiling.h"
}

namespace Datadog {

// Uploader handles uploading encoded profiles to the Datadog backend.
// Upload state (lock, cancellation token, sequence number) is stored in the ProfilerState singleton.
//
// The Uploader does NOT own the exporter — it borrows a reference to the one
// cached in ProfilerState. The encoded profile is owned and dropped in the destructor.
class Uploader
{
  private:
    std::string errmsg;
    std::string output_filename;
    ddog_prof_ProfileExporter& ddog_exporter; // borrowed from ProfilerState
    ddog_prof_EncodedProfile encoded_profile{};
    Datadog::ProfilerStats profiler_stats;
    std::string process_tags;

    bool export_to_file(ddog_prof_EncodedProfile& encoded, std::string_view internal_metadata_json);

  public:
    bool upload();
    bool upload_unlocked(); // Version that assumes lock is already held
    static void cancel_inflight();
    static void lock();
    static void unlock();

    Uploader(std::string_view _url,
             ddog_prof_ProfileExporter& exporter_ref,
             ddog_prof_EncodedProfile encoded,
             Datadog::ProfilerStats stats,
             std::string_view _process_tags);
    ~Uploader();

    // Non-copyable (encoded_profile is a unique resource)
    Uploader(const Uploader&) = delete;
    Uploader& operator=(const Uploader&) = delete;

    // Move support — transfers ownership of encoded_profile only.
    // The exporter reference stays valid (it's in ProfilerState).
    Uploader(Uploader&& other) noexcept
      : ddog_exporter(other.ddog_exporter)
    {
        encoded_profile = other.encoded_profile;
        other.encoded_profile = { .inner = nullptr };
        profiler_stats = other.profiler_stats;
        output_filename = std::move(other.output_filename);
        errmsg = std::move(other.errmsg);
        process_tags = std::move(other.process_tags);
    }

    Uploader& operator=(Uploader&& other) noexcept = delete;
};

} // namespace Datadog
