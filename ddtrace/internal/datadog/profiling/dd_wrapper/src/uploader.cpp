#include "uploader.hpp"
#include "libdatadog_helpers.hpp"

using namespace Datadog;

void
DdogCancellationTokenDeleter::operator()(ddog_CancellationToken* ptr) const
{
    if (ptr != nullptr) {
        ddog_CancellationToken_cancel(ptr);
        ddog_CancellationToken_drop(ptr);
    }
}

Datadog::Uploader::Uploader(std::string_view _url, ddog_prof_Exporter* _ddog_exporter)
  : url{ _url }
  , ddog_exporter{ _ddog_exporter }
{
}

bool
Datadog::Uploader::upload(ddog_prof_Profile& profile)
{
    // Serialize the profile
    ddog_prof_Profile_SerializeResult result = ddog_prof_Profile_serialize(&profile, nullptr, nullptr, nullptr);
    if (result.tag != DDOG_PROF_PROFILE_SERIALIZE_RESULT_OK) { // NOLINT (cppcoreguidelines-pro-type-union-access)
        auto err = result.err;                                 // NOLINT (cppcoreguidelines-pro-type-union-access)
        errmsg = err_to_msg(&err, "Error serializing pprof");
        std::cerr << errmsg << std::endl;
        ddog_Error_drop(&err);
        return false;
    }
    ddog_prof_EncodedProfile* encoded = &result.ok; // NOLINT (cppcoreguidelines-pro-type-union-access)

    // If we have any custom tags, set them now
    ddog_Vec_Tag tags = ddog_Vec_Tag_new();
    add_tag(tags, ExportTagKey::runtime_id, runtime_id, errmsg);

    // Build the request object
    const ddog_prof_Exporter_File file = {
        .name = to_slice("auto.pprof"),
        .file = ddog_Vec_U8_as_slice(&encoded->buffer),
    };
    const uint64_t max_timeout_ms = 5000; // 5s is a common timeout parameter for Datadog profilers
    auto build_res = ddog_prof_Exporter_Request_build(ddog_exporter.get(),
                                                      encoded->start,
                                                      encoded->end,
                                                      ddog_prof_Exporter_Slice_File_empty(),
                                                      { .ptr = &file, .len = 1 },
                                                      &tags,
                                                      nullptr,
                                                      nullptr,
                                                      nullptr,
                                                      max_timeout_ms);
    ddog_prof_EncodedProfile_drop(encoded);

    if (build_res.tag ==
        DDOG_PROF_EXPORTER_REQUEST_BUILD_RESULT_ERR) { // NOLINT (cppcoreguidelines-pro-type-union-access)
        auto err = build_res.err;                      // NOLINT (cppcoreguidelines-pro-type-union-access)
        errmsg = err_to_msg(&err, "Error building request");
        std::cerr << errmsg << std::endl;
        ddog_Error_drop(&err);
        ddog_Vec_Tag_drop(tags);
        return false;
    }

    // If we're here, we're about to create a new upload, so cancel any inflight ones
    cancel_inflight();

    // Create a new cancellation token.  Maybe we can get away without doing this, but
    // since we're recreating the uploader fresh every time anyway, we recreate one more thing.
    // NB wrapping this in a unique_ptr to easily add RAII semantics; maybe should just wrap it in a
    // class instead
    cancel.reset(ddog_CancellationToken_new());
    std::unique_ptr<ddog_CancellationToken, DdogCancellationTokenDeleter> cancel_for_request;
    cancel_for_request.reset(ddog_CancellationToken_clone(cancel.get()));

    // The upload operation sets up some global state in libdatadog (the tokio runtime), so
    // we ensure exclusivity here.
    {
        const std::lock_guard<std::mutex> lock_guard(upload_lock);

        // Build and check the response object
        ddog_prof_Exporter_Request* req = build_res.ok; // NOLINT (cppcoreguidelines-pro-type-union-access)
        ddog_prof_Exporter_SendResult res =
          ddog_prof_Exporter_send(ddog_exporter.get(), &req, cancel_for_request.get());
        if (res.tag == DDOG_PROF_EXPORTER_SEND_RESULT_ERR) { // NOLINT (cppcoreguidelines-pro-type-union-access)
            auto err = res.err;                              // NOLINT (cppcoreguidelines-pro-type-union-access)
            errmsg = err_to_msg(&err, "Error uploading");
            std::cerr << errmsg << std::endl;
            ddog_Error_drop(&err);
            ddog_Vec_Tag_drop(tags);
            return false;
        }
        ddog_prof_Exporter_Request_drop(&req);
    }

    // Cleanup
    ddog_Vec_Tag_drop(tags);
    return true;
}

void
Datadog::Uploader::lock()
{
    upload_lock.lock();
}

void
Datadog::Uploader::unlock()
{
    upload_lock.unlock();
}

void
Datadog::Uploader::cancel_inflight()
{
    cancel.reset();
}

void
Datadog::Uploader::prefork()
{
    lock();
    cancel_inflight();
}

void
Datadog::Uploader::postfork_parent()
{
    unlock();
}

void
Datadog::Uploader::postfork_child()
{
    unlock();
}
