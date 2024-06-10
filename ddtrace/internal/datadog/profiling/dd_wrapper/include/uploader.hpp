#pragma once

#include "sample.hpp"
#include "types.hpp"

#include <memory>
#include <mutex>
#include <string_view>

extern "C"
{
#include "datadog/profiling.h"
}

namespace Datadog {

struct DdogProfExporterDeleter
{
    void operator()(ddog_prof_Exporter* ptr) const;
};

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
    std::unique_ptr<ddog_prof_Exporter, DdogProfExporterDeleter> ddog_exporter;
    std::string runtime_id;
    uint64_t upload_seq = 0;

  public:
    bool upload(ddog_prof_Profile& profile);
    bool pprof_to_disk(ddog_prof_Profile& profile, int dirfd);
    static void cancel_inflight();
    static void lock();
    static void unlock();
    static void prefork();
    static void postfork_parent();
    static void postfork_child();

    Uploader(ddog_prof_Exporter* ddog_exporter, std::string_view _runtime_id, uint64_t _upload_seq);
};

} // namespace Datadog
