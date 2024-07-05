// This repo can't currently use std::filesystem or any nice APIs, so let's wrap some
// C APIs here
#pragma once

namespace Datadog {

class FileDescriptor
{
    int fd;

public:

    explicit FileDescriptor(int _fd);

    operator int() const;

    ~FileDescriptor();

    // getters
    int get() const;
    bool is_valid() const;

    // Utilities
    static FileDescriptor open(std::string_view path, int flags, mode_t mode);
    static FileDescriptor openat(int dirfd, std::string_view path, int flags, mode_t mode);
};

} // namespace Datadog
