#pragma once

#include "sample.hpp"
#include "types.hpp"

#include <atomic>
#include <memory>
#include <mutex>

extern "C"
{
#include "datadog/profiling.h"
}

namespace Datadog {

struct DdogCancellationTokenDeleter
{
    void operator()(ddog_CancellationToken* ptr) const;
};

class Uploader
{
  private:
    static inline std::mutex upload_lock{};
    std::string errmsg;
    static inline std::unique_ptr<ddog_CancellationToken, DdogCancellationTokenDeleter> cancel;
    static inline std::atomic<uint64_t> upload_seq{ 0 };
    std::string output_filename;
    std::unique_ptr<ddog_prof_ProfileExporter, DdogProfExporterDeleter> ddog_exporter;

    bool export_to_file(ddog_prof_EncodedProfile* encoded);

  public:
    bool upload(ddog_prof_Profile& profile);
    static void cancel_inflight();
    static void lock();
    static void unlock();
    static void prefork();
    static void postfork_parent();
    static void postfork_child();

    Uploader(std::string_view _url, ddog_prof_ProfileExporter* ddog_exporter);
};

} // namespace Datadog
