#include "uploader_builder.hpp"
#include "libdatadog_helpers.hpp"

#include <numeric>
#include <utility>
#include <vector>

#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

void
Datadog::UploaderBuilder::set_env(std::string_view _env)
{
    if (!_env.empty()) {
        profile_metadata.env = _env;
    }
}

void
Datadog::UploaderBuilder::set_service(std::string_view _service)
{
    if (!_service.empty()) {
        profile_metadata.service = _service;
    }
}

void
Datadog::UploaderBuilder::set_version(std::string_view _version)
{
    if (!_version.empty()) {
        profile_metadata.version = _version;
    }
}

void
Datadog::UploaderBuilder::set_runtime(std::string_view _runtime)
{
    if (!_runtime.empty()) {
        profile_metadata.runtime = _runtime;
    }
}

void
Datadog::UploaderBuilder::set_runtime_version(std::string_view _runtime_version)
{
    if (!_runtime_version.empty()) {
        profile_metadata.runtime_version = _runtime_version;
    }
}

void
Datadog::UploaderBuilder::set_profiler_version(std::string_view _profiler_version)
{
    if (!_profiler_version.empty()) {
        profile_metadata.profiler_version = _profiler_version;
    }
}

void
Datadog::UploaderBuilder::set_url(std::string_view _url)
{
    if (!_url.empty()) {
        profile_metadata.url = _url;
    }
}

void
Datadog::UploaderBuilder::clear_url()
{
    profile_metadata.url = "";
}

bool
Datadog::UploaderBuilder::set_dir(std::string_view _dir)
{
    if (_dir.empty()) {
        return false;
    }

    // Given a directory, we need to ensure that it
    // - Exists
    // - Is writable by the current user
    // We'll do this by attempting to create a file in the directory
    // and then deleting it.  If we can't do that, we'll throw an error.
    const char *file_cstr = ".dd_test_file";
    const char *dir_cstr = _dir.data();

    // this will be a real file and not a FIFO, so we don't need to check EINTR
    int dirfd = open(dir_cstr, O_DIRECTORY);
    if (dirfd == -1) {
        return false;
    }
    int fd = openat(dirfd, file_cstr, O_CREAT | O_WRONLY, S_IRUSR | S_IWUSR);
    close(dirfd);
    unlink(file_cstr);
    if (fd == -1) {
        return false;
    }

    // Usually just creating a file is sufficient, but SELinux is a thing, so write too.
    size_t bytes_written = 0;
    do {
        bytes_written = write(fd, "test", 4);
    } while (errno == EINTR);
    close(fd);

    if (bytes_written != 4) {
        return false;
    }

    // If we're here, we can write to the directory, so we'll save it.
    profile_metadata.dir = _dir;
    return true;
}

void
Datadog::UploaderBuilder::clear_dir()
{
    profile_metadata.dir = "";
}

void
Datadog::UploaderBuilder::set_tag(std::string_view _key, std::string_view _val)
{

    if (!_key.empty() && !_val.empty()) {
        const std::lock_guard<std::mutex> lock(tag_mutex);
        profile_metadata.user_tags[std::string(_key)] = std::string(_val);
    }
}

void
Datadog::UploaderBuilder::set_runtime_id(std::string_view _runtime_id)
{
    if (!_runtime_id.empty()) {
        // If the runtime_id is the same, we don't need to do anything.
        // if it's different, change and also reset the upload counter (new runtime, new sequence)
        if (profile_metadata.runtime_id != _runtime_id) {
            profile_metadata.runtime_id = _runtime_id;
            upload_seq.store(0);
        }
    }
}

std::string
join(const std::vector<std::string>& vec, const std::string& delim)
{
    return std::accumulate(vec.begin(),
                           vec.end(),
                           std::string(),
                           [&delim](const std::string& left, const std::string& right) -> std::string {
                               // If the left and right operands are empty, we don't want to add a delimiter
                               if (left.empty()) {
                                   return right;
                               }
                               if (right.empty()) {
                                   return left;
                               }
                               return left + delim + right;
                           });
}

std::variant<Datadog::Uploader, std::string>
Datadog::UploaderBuilder::build()
{
    // Increment the upload sequence number every time we build an uploader; in the current
    // code Uploaders are use-once-and-destroy, so this pans out.
    upload_seq++;

    // Setup the ddog_Exporter
    ddog_Vec_Tag tags = ddog_Vec_Tag_new();

    // Add the tags.  In the average case, the user has a structural problem with
    // one of their tags, but it's really annoying to have to iteratively fix several
    // tags, so we'll just collect all the reasons and report them all at once.
    std::vector<std::string> reasons{};
    const std::vector<std::pair<ExportTagKey, std::string_view>> tag_data = {
        { ExportTagKey::dd_env, profile_metadata.env },
        { ExportTagKey::service, profile_metadata.service },
        { ExportTagKey::version, profile_metadata.version },
        { ExportTagKey::language, profile_metadata.family },
        { ExportTagKey::runtime, profile_metadata.runtime },
        { ExportTagKey::runtime_version, profile_metadata.runtime_version },
        { ExportTagKey::profiler_version, profile_metadata.profiler_version },
    };

    for (const auto& [tag, data] : tag_data) {
        if (!data.empty()) {
            std::string errmsg;
            if (!add_tag(tags, tag, data, errmsg)) {
                reasons.push_back(std::string(to_string(tag)) + ": " + errmsg);
            }
        }
    }

    // Add the user-defined tags, if any.
    for (const auto& tag : profile_metadata.user_tags) {
        std::string errmsg;
        if (!add_tag(tags, tag.first, tag.second, errmsg)) {
            reasons.push_back(std::string(tag.first) + ": " + errmsg);
        }
    }

    if (!reasons.empty()) {
        ddog_Vec_Tag_drop(tags);
        return "Error initializing exporter, missing or bad configuration: " + join(reasons, ", ");
    }

    // If we're here, the tags are good, so we can initialize the exporter
    ddog_prof_Exporter_NewResult res = ddog_prof_Exporter_new(to_slice("dd-trace-py"),
                                                              to_slice(profile_metadata.profiler_version),
                                                              to_slice(profile_metadata.family),
                                                              &tags,
                                                              ddog_prof_Endpoint_agent(to_slice(profile_metadata.url)));
    ddog_Vec_Tag_drop(tags);

    auto ddog_exporter_result = Datadog::get_newexporter_result(res);
    ddog_prof_Exporter* ddog_exporter = nullptr;
    if (std::holds_alternative<ddog_prof_Exporter*>(ddog_exporter_result)) {
        ddog_exporter = std::get<ddog_prof_Exporter*>(ddog_exporter_result);
    } else {
        auto& err = std::get<ddog_Error>(ddog_exporter_result);
        std::string errmsg = Datadog::err_to_msg(&err, "Error initializing exporter");
        ddog_Error_drop(&err); // errmsg contains a copy of err.message
        return errmsg;
    }

    auto uploader = Datadog::Uploader{ ddog_exporter, profile_metadata };
    uploader.upload_seq = upload_seq.load();
    return "hello";
}
