#include "uploader.hpp"
#include "libdatadog_helpers.hpp"

using namespace Datadog;

void
DdogProfExporterDeleter::operator()(ddog_prof_Exporter* ptr) const
{
    ddog_prof_Exporter_drop(ptr);
}

Uploader::Uploader(std::string_view _url, ddog_prof_Exporter* _ddog_exporter)
  : url{ _url }
  , ddog_exporter{ _ddog_exporter }
{}

bool
Uploader::upload(ddog_prof_Profile& profile)
{
    // Serialize the profile
    ddog_prof_Profile_SerializeResult result = ddog_prof_Profile_serialize(&profile, nullptr, nullptr, nullptr);
    if (result.tag != DDOG_PROF_PROFILE_SERIALIZE_RESULT_OK) {
        errmsg = err_to_msg(&result.err, "Error serializing pprof");
        std::cerr << errmsg << std::endl;
        ddog_Error_drop(&result.err);
        return false;
    }
    ddog_prof_EncodedProfile* encoded = &result.ok;

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
                                                      max_timeout_ms);
    ddog_prof_EncodedProfile_drop(encoded);

    if (build_res.tag == DDOG_PROF_EXPORTER_REQUEST_BUILD_RESULT_ERR) {
        errmsg = err_to_msg(&build_res.err, "Error building request");
        std::cerr << errmsg << std::endl;
        ddog_Error_drop(&build_res.err);
        ddog_Vec_Tag_drop(tags);
        return false;
    }

    // If we're here, we're about to create a new upload, so cancel any inflight ones
    cancel_inflight();

    // Create a new cancellation token.  Maybe we can get away without doing this, but
    // since we're recreating the uploader fresh every time anyway, we recreate one more thing.
    // NB `cancel_inflight()` already drops the old token.
    cancel = ddog_CancellationToken_new();
    auto* cancel_for_request = ddog_CancellationToken_clone(cancel);

    // The upload operation sets up some global state in libdatadog (the tokio runtime), so
    // we ensure exclusivity here.
    {
        const std::lock_guard<std::mutex> lock_guard(upload_lock);

        // Build and check the response object
        ddog_prof_Exporter_Request* req = build_res.ok;
        ddog_prof_Exporter_SendResult res = ddog_prof_Exporter_send(ddog_exporter.get(), &req, cancel_for_request);
        if (res.tag == DDOG_PROF_EXPORTER_SEND_RESULT_ERR) {
            errmsg = err_to_msg(&res.err, "Error uploading");
            std::cerr << errmsg << std::endl;
            ddog_Error_drop(&res.err);
            ddog_Vec_Tag_drop(tags);
            return false;
        }
        ddog_prof_Exporter_Request_drop(&req);
    }

    // Cleanup
    ddog_Vec_Tag_drop(tags);
    ddog_CancellationToken_drop(cancel_for_request);

    return true;
}

void
Uploader::lock()
{
    upload_lock.lock();
}

void
Uploader::unlock()
{
    upload_lock.unlock();
}

void
Uploader::cancel_inflight()
{
    // According to the rust docs, the `cancel()` call is synchronous
    // https://docs.rs/tokio-util/latest/tokio_util/sync/struct.CancellationToken.html#method.cancel
    if (cancel) {
        ddog_CancellationToken_cancel(cancel);
        ddog_CancellationToken_drop(cancel);
    }
    cancel = nullptr;
}

void
Uploader::prefork()
{
    lock();
    cancel_inflight();
}

void
Uploader::postfork_parent()
{
    unlock();
}

void
Uploader::postfork_child()
{
    unlock();
}
