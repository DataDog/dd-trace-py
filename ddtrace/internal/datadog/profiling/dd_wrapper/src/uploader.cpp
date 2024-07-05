#include "uploader.hpp"
#include "libdatadog_helpers.hpp"
#include "util/file.hpp"

using namespace Datadog;

void
DdogCancellationTokenDeleter::operator()(ddog_CancellationToken* ptr) const
{
    if (ptr != nullptr) {
        ddog_CancellationToken_cancel(ptr);
        ddog_CancellationToken_drop(ptr);
    }
}

Datadog::Uploader::Uploader(std::string_view _url, std::string_view _dir, ddog_prof_Exporter* _ddog_exporter, uint64_t _upload_seq)
  : url{ _url }
  , dir{ _dir }
  , ddog_exporter{ _ddog_exporter }
  , upload_seq{ _upload_seq }
{
}

bool
Datadog::Uploader::export_to_file(ddog_prof_EncodedProfile* encoded)
{

    // Temporary
    std::string pprof_name = "auto.pprof";
    std::string tags_name = "auto.tags";

    const char *buffer = encoded->buffer.ptr;
    const size_t len = encoded->buffer.len;
    auto dir_fd = FileDescriptor::open(dir, O_DIRECTORY | O_RDONLY);
    if (dir_fd == -1) {
        std::cerr << "Error opening directory " << dir << ": " << strerror(errno) << std::endl;
        return false;
    }

    auto pprof_fd = FileDescriptor::openat(dir_fd, pprof_name, O_CREAT | O_WRONLY | O_TRUNC, 0644);
    if (pprof_fd == -1) {
        std::cerr << "Error opening file " << file << ": " << strerror(errno) << std::endl;
        return false;
    }

    // Write the buffer to the pprof file
    ssize_t written = write(pprof_fd, buffer, len);
    if (written == -1) {
        std::cerr << "Error writing to file " << pprof_name << ": " << strerror(errno) << std::endl;
        unlinkat(dir_fd, file, 0);
        return false;
    }

    // Done!
    return true;
}

bool
Datadog::Uploader::export_to_url(ddog_prof_EncodedProfile* encoded)
{
}

bool
Datadog::Uploader::upload(ddog_prof_Profile& profile)
{
    // Before we do anything, we can only upload if we have a valid dir or url.
    // If this were a generic application, then we'd need to finely control how we deal with these, since a
    // set/unset/set sequence would leave an invalid middle state, but the calling code will ensure these are only set
    // once near config time.
    bool ret = false;
    if (dir.empty() && url.empty()) {
        std::cerr << "No upload destination set" << std::endl;
        return ret;
    }

    // If we're here, we can safely serialize the profile.
    ddog_prof_Profile_SerializeResult result = ddog_prof_Profile_serialize(&profile, nullptr, nullptr, nullptr);
    if (result.tag != DDOG_PROF_PROFILE_SERIALIZE_RESULT_OK) { // NOLINT (cppcoreguidelines-pro-type-union-access)
        auto err = result.err;                                 // NOLINT (cppcoreguidelines-pro-type-union-access)
        errmsg = err_to_msg(&err, "Error serializing pprof");
        std::cerr << errmsg << std::endl;
        ddog_Error_drop(&err);
        return ret;
    }
    ddog_prof_EncodedProfile* encoded = &result.ok; // NOLINT (cppcoreguidelines-pro-type-union-access)

    // If we have a directory, then write file to disk
    if (!dir.empty()) {
        ret = write_to_file(encoded, dir, "auto.pprof"); // TODO combine the sequence number and runtime id
    }

    // If we don't have a URL, we're done.
    if (url.empty()) {
        ddog_prof_EncodedProfile_drop(encoded);
        return ret;
    }

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
