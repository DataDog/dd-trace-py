#include <errno.h> // errno
#include <fcntl.h> // O_CREAT, O_WRONLY, O_TRUNC
#include <string.h> // strerror
#include <sys/stat.h> // S_IRUSR, S_IWUSR, S_IRGRP, S_IROTH
#include <unistd.h> // write
#include <iostream>

#include "uploader.hpp"
#include "libdatadog_helpers.hpp"
#include "util/file.hpp"

void
Datadog::DdogCancellationTokenDeleter::operator()(ddog_CancellationToken* ptr) const
{
    if (ptr != nullptr) {
        ddog_CancellationToken_cancel(ptr);
        ddog_CancellationToken_drop(ptr);
    }
}

Datadog::Uploader::Uploader(ddog_prof_Exporter *_ddog_exporter, const UploaderTags& _profile_metadata)
  : ddog_exporter{_ddog_exporter}
  , profile_metadata{_profile_metadata}
{
}

bool
Datadog::Uploader::write_buffer(int dirfd, std::string_view filename, const char *buffer, size_t len)
{

    auto fd = FileDescriptor::openat(dirfd, filename, O_CREAT | O_WRONLY | O_TRUNC, 0644);
    if (fd == -1) {
        std::cerr << "Error opening file " << filename << ": " << strerror(errno) << std::endl;
        return false;
    }

    // Write the buffer to the file
    ssize_t written = write(fd, buffer, len);
    if (written == -1) {
        std::cerr << "Error writing to file " << filename << ": " << strerror(errno) << std::endl;
        FileDescriptor::unlinkat(dirfd, filename, 0);
        return false;
    }

    // Done
    return true;
}

bool
Datadog::Uploader::write_tags(const std::vector<std::pair<std::string_view, std::string_view>>& tags, int dirfd, std::string_view filename)
{
    // Serialize the tags to a json string
    // There are a ton of libraries to do this, but this is a one-off thing anyway so whatever
    std::string json = "{";
    for (size_t i = 0; i < tags.size(); i++) {
        json += "\"" + std::string(tags[i].first) + "\": \"" + std::string(tags[i].second) + "\"";
        json += tags.size() - 1 == i ? "" : ", ";
    }
    json += "}";

    // Write the json to the file
    return write_buffer(dirfd, filename, json.c_str(), json.size());
}

bool
Datadog::Uploader::export_to_file(ddog_prof_EncodedProfile* encoded)
{

    // Temporary
    bool ret = false;
    std::string tags_name = "auto.tags";

    // Open a dirfd for the later operations
    auto dirfd = FileDescriptor::open(profile_metadata.dir, O_DIRECTORY | O_RDONLY, 0644);
    if (dirfd == -1) {
        std::cerr << "Error opening directory " << profile_metadata.dir << ": " << strerror(errno) << std::endl;
        return false;
    }

    // pprof data gets written to its own file
    const char *buffer = reinterpret_cast<const char *>(encoded->buffer.ptr); // conform with write_buffer signature
    const size_t len = encoded->buffer.len;
    ret = write_buffer(dirfd, "auto.pprof", buffer, len);

    // Tags get serialized as json to another file
    std::vector<std::pair<std::string_view, std::string_view>> tags = {
        { "env", profile_metadata.env },
        { "service", profile_metadata.service },
        { "version", profile_metadata.version },
        { "runtime_version", profile_metadata.runtime_version },
        { "runtime_id", profile_metadata.runtime_id },
        { "profiler_version", profile_metadata.profiler_version },
        { "upload_seq", std::to_string(upload_seq) },
        { "runtime", profile_metadata.runtime },
        { "family", profile_metadata.family },
        { "url", profile_metadata.url },
        { "dir", profile_metadata.dir }
    };

    // Add user tags
    for (const auto& [key, val] : profile_metadata.user_tags) {
        tags.push_back({ key, val });
    }

    // And write them
    ret ^= write_tags(tags, dirfd, tags_name);

    // Done with the pprof
    // TODO disambiguate the return?
    return ret;
}

bool
Datadog::Uploader::export_to_url(ddog_prof_EncodedProfile* encoded)
{
  (void)encoded;
  return false;
}

bool
Datadog::Uploader::upload(ddog_prof_Profile& profile)
{
    // Before we do anything, we can only upload if we have a valid dir or url.
    // If this were a generic application, then we'd need to finely control how we deal with these, since a
    // set/unset/set sequence would leave an invalid middle state, but the calling code will ensure these are only set
    // once near config time.
    bool ret = false;
    if (profile_metadata.dir.empty() && profile_metadata.url.empty()) {
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
    if (!profile_metadata.dir.empty()) {
        ret = export_to_file(encoded);
    }

    // If we don't have a URL, we're done.
    if (profile_metadata.url.empty()) {
        ddog_prof_EncodedProfile_drop(encoded);
        return ret;
    }

    // If we have any custom tags, set them now
    ddog_Vec_Tag tags = ddog_Vec_Tag_new();
    add_tag(tags, ExportTagKey::runtime_id, profile_metadata.runtime_id, errmsg);

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
