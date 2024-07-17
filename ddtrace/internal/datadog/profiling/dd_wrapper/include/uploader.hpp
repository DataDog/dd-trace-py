#pragma once

#include "constants.hpp"
#include "sample.hpp"
#include "types.hpp"

#include <memory>
#include <mutex>
#include <string>
#include <string_view>
#include <unordered_map>

extern "C"
{
#include "datadog/profiling.h"
}

namespace Datadog {

// Forward decl
class UploaderBuilder;


// env, service, version, and either url/dir must be specified upstream
// It's tempting to factor this to be closer to the point of consumption (either the uploader or the builder), but
// because we allow these values to get serialized by the uploader, and they need to be referenced in the builder,
// _and_ because we have to support forks + threads, just make the whole thing easy by copying the bundle in the
// uploader.  We can deal with copying a ~dozen strings once per minute.
struct UploaderTags {
    using ExporterTagset = std::unordered_map<std::string, std::string>;

    std::string env = "";
    std::string service = "";
    std::string version = "";
    std::string url = "";
    std::string dir = "";
    std::string runtime_version = "";
    std::string runtime_id = "";
    std::string profiler_version = "";
    ExporterTagset user_tags{};

    std::string_view runtime{ "cython" };
    std::string_view family{ "python" };
};

struct DdogCancellationTokenDeleter
{
    void operator()(ddog_CancellationToken* ptr) const;
};

class Uploader
{
  private:
    static inline std::mutex upload_lock{};
    static inline std::unique_ptr<ddog_CancellationToken, DdogCancellationTokenDeleter> cancel;
    std::unique_ptr<ddog_prof_Exporter, DdogProfExporterDeleter> ddog_exporter;
    std::string errmsg;
    const UploaderTags profile_metadata;
    uint64_t upload_seq = 0;

    // Can only be built by an UploaderBuilder
    friend class UploaderBuilder;
    Uploader(ddog_prof_Exporter* _ddog_exporter, const UploaderTags& _profile_metadata);

    // Helper functions
    bool write_buffer(int dirfd, std::string_view filename, const char* buffer, size_t len);
    bool write_tags(const std::vector<std::pair<std::string_view, std::string_view>>& tags, int dirfd, std::string_view filename);
    bool export_to_file(ddog_prof_EncodedProfile* encoded);
    bool export_to_url(ddog_prof_EncodedProfile* encoded);

  public:
    bool upload(ddog_prof_Profile& profile);
    static void cancel_inflight();
    static void lock();
    static void unlock();
    static void prefork();
    static void postfork_parent();
    static void postfork_child();
};

} // namespace Datadog
