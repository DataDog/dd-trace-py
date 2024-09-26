#include "uploader.hpp"

#include "code_provenance.hpp"
#include "libdatadog_helpers.hpp"

#include <errno.h> // errno
#include <fstream> // ofstream
#include <optional>
#include <sstream>  // ostringstream
#include <string.h> // strerror
#include <unistd.h> // getpid
#include <vector>

using namespace Datadog;

void
DdogCancellationTokenDeleter::operator()(ddog_CancellationToken* ptr) const
{
    if (ptr != nullptr) {
        ddog_CancellationToken_cancel(ptr);
        ddog_CancellationToken_drop(ptr);
    }
}

Datadog::Uploader::Uploader(std::string_view _output_filename, ddog_prof_Exporter* _ddog_exporter)
  : output_filename{ _output_filename }
  , ddog_exporter{ _ddog_exporter }
{
    // Increment the upload sequence number every time we build an uploader.
    // Upoloaders are use-once-and-destroy.
    upload_seq++;
}

bool
Datadog::Uploader::export_to_file(ddog_prof_EncodedProfile* encoded)
{
    // Write the profile to a file using the following format for filename:
    // <output_filename>.<process_id>.<sequence_number>
    std::ostringstream oss;
    oss << output_filename << "." << getpid() << "." << upload_seq;
    std::string filename = oss.str();
    std::ofstream out(filename, std::ios::binary);
    if (!out.is_open()) {
        std::cerr << "Error opening output file " << filename << ": " << strerror(errno) << std::endl;
        return false;
    }
    out.write(reinterpret_cast<const char*>(encoded->buffer.ptr), encoded->buffer.len);
    if (out.fail()) {
        std::cerr << "Error writing to output file " << filename << ": " << strerror(errno) << std::endl;
        return false;
    }
    return true;
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

    if (!output_filename.empty()) {
        bool ret = export_to_file(encoded);
        ddog_prof_EncodedProfile_drop(encoded);
        return ret;
    }

    std::vector<ddog_prof_Exporter_File> files_to_send = { {
      .name = to_slice("auto.pprof"),
      .file = ddog_Vec_U8_as_slice(&encoded->buffer),
    } };

    // DEV: This function is called with the profile_lock held, and the following
    // function call acquires lock on CodeProvenance.
    std::optional<std::string> json_str_opt = CodeProvenance::get_instance().try_serialize_to_json_str();
    if (json_str_opt.has_value() and !json_str_opt.value().empty()) {
        files_to_send.push_back({
          .name = to_slice("code-provenance.json"),
          .file = to_byte_slice(json_str_opt.value()),
        });
    }

    auto build_res =
      ddog_prof_Exporter_Request_build(ddog_exporter.get(),
                                       encoded->start,
                                       encoded->end,
                                       ddog_prof_Exporter_Slice_File_empty(),
                                       { .ptr = reinterpret_cast<const ddog_prof_Exporter_File*>(files_to_send.data()),
                                         .len = static_cast<uintptr_t>(files_to_send.size()) },
                                       nullptr,
                                       encoded->endpoints_stats,
                                       nullptr,
                                       nullptr);
    ddog_prof_EncodedProfile_drop(encoded);

    if (build_res.tag ==
        DDOG_PROF_EXPORTER_REQUEST_BUILD_RESULT_ERR) { // NOLINT (cppcoreguidelines-pro-type-union-access)
        auto err = build_res.err;                      // NOLINT (cppcoreguidelines-pro-type-union-access)
        errmsg = err_to_msg(&err, "Error building request");
        std::cerr << errmsg << std::endl;
        ddog_Error_drop(&err);
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
            return false;
        }
        ddog_prof_Exporter_Request_drop(&req);
    }

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
