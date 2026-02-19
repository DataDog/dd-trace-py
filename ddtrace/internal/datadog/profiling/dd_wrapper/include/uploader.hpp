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
class Uploader
{
  private:
    std::string errmsg;
    std::string output_filename;
    ddog_prof_ProfileExporter ddog_exporter{ .inner = nullptr };
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
             ddog_prof_ProfileExporter ddog_exporter,
             ddog_prof_EncodedProfile encoded,
             Datadog::ProfilerStats stats,
             std::string_view _process_tags);
    ~Uploader();

    // Disable copy constructor and copy assignment operator to avoid double-free
    // of ddog_exporter
    Uploader(const Uploader&) = delete;
    Uploader& operator=(const Uploader&) = delete;

    // In move constructor and move assignment operator, we clear inner pointer
    // of ddog_exporter in other to avoid double-free from the destructor.
    // These were added as we started to calling ddog_prof_Exporter_drop()
    // function in the destructor.
    // We initially observed the double free error as we created a temporary
    // object which then moved to std::variant. A simplified example and possible
    // solution using std::variant constructor is here:
    // https://gist.github.com/taegyunkim/9191e643e315be55e78e383ccc498713
    // We also update the move constructor and move assignment operator to set
    // the inner pointer to nullptr to avoid double-free. At the time of writing,
    // we don't have code that uses move constructor and move assignment operator,
    // but we add them to avoid any potential issues.
    Uploader(Uploader&& other) noexcept
    {
        ddog_exporter = other.ddog_exporter;
        other.ddog_exporter = { .inner = nullptr };
        encoded_profile = other.encoded_profile;
        other.encoded_profile = { .inner = nullptr };
        profiler_stats = other.profiler_stats;
        output_filename = std::move(other.output_filename);
        errmsg = std::move(other.errmsg);
        process_tags = std::move(other.process_tags);
    }

    Uploader& operator=(Uploader&& other) noexcept
    {
        if (this != &other) {
            ddog_prof_Exporter_drop(&ddog_exporter);
            ddog_prof_EncodedProfile_drop(&encoded_profile);
            ddog_exporter = other.ddog_exporter;
            other.ddog_exporter = { .inner = nullptr };
            encoded_profile = other.encoded_profile;
            other.encoded_profile = { .inner = nullptr };
            profiler_stats = other.profiler_stats;
            output_filename = std::move(other.output_filename);
            errmsg = std::move(other.errmsg);
            process_tags = std::move(other.process_tags);
        }
        return *this;
    }
};

} // namespace Datadog
