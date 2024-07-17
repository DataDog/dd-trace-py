#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>

#include "util/file.hpp"


Datadog::FileDescriptor::FileDescriptor(int _fd)
  : fd{ _fd }
{
}

Datadog::FileDescriptor::operator int() const
{
    return fd;
}

Datadog::FileDescriptor::~FileDescriptor()
{
    close(fd);
}

int
Datadog::FileDescriptor::get() const
{
    return fd;
}

bool
Datadog::FileDescriptor::is_valid() const
{
    return fd != -1;
}

static Datadog::FileDescriptor
Datadog::FileDescriptor::open(std::string_view path, int flags, mode_t mode)
{
    int fd = open(path.data(), flags, mode);
    return FileDescriptor{ fd };
}

static Datadog::FileDescriptor
Datadog::FileDescriptor::openat(int dirfd, std::string_view path, int flags, mode_t mode)
{
    int fd = openat(dirfd, path.data(), flags, mode);
    return FileDescriptor{ fd };
}

bool
Datadog::FileDescriptor::unlinkat(int dirfd, std::string_view path, int flags)
{
    return unlinkat(dirfd, path.data(), flags) == 0;
}
