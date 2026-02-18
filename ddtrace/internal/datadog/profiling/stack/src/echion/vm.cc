#include <echion/vm.h>

#include <algorithm>
#include <array>
#include <iostream>
#include <string>

#include <cstdint>

bool
is_truthy(const char* s)
{
    const static std::array<std::string, 6> truthy_values = { "1", "true", "yes", "on", "enable", "enabled" };

    return std::find(truthy_values.begin(), truthy_values.end(), s) != truthy_values.end();
}

bool
use_alternative_copy_memory()
{
    const char* use_fast_copy_memory = std::getenv("ECHION_USE_FAST_COPY_MEMORY");
    if (!use_fast_copy_memory) {
        return false;
    }

    if (is_truthy(use_fast_copy_memory)) {
        return true;
    }

    return false;
}

#if defined PL_LINUX
VmReader*
VmReader::create(size_t sz)
{
    // Makes a temporary file and ftruncates it to the specified size
    std::array<std::string, 3> tmp_dirs = { "/dev/shm", "/tmp", "/var/tmp" };
    std::string tmp_suffix = "/echion-XXXXXX";

    int fd = -1;
    void* ret = nullptr;

    for (auto& tmp_dir : tmp_dirs) {
        // Reset the file descriptor, just in case
        close(fd);

        // Create the temporary file
        std::string tmpfile = tmp_dir + tmp_suffix;
        fd = mkstemp(tmpfile.data());
        if (fd == -1)
            continue;

        // Unlink might fail if delete is blocked on the VFS, but currently no action is taken
        unlink(tmpfile.data());

        // Make sure we have enough size
        if (ftruncate(fd, sz) == -1) {
            continue;
        }

        // Map the file
        ret = mmap(NULL, sz, PROT_READ | PROT_WRITE, MAP_PRIVATE, fd, 0);
        if (ret == MAP_FAILED) {
            ret = nullptr;
            continue;
        }

        // Successful.  Break.
        break;
    }

    return new VmReader(sz, ret, fd);
}

namespace {
constexpr size_t KB = 1024;
constexpr size_t MB = KB * KB;
} // namespace

VmReader*
VmReader::get_instance()
{
    if (instance == nullptr) {
        instance = VmReader::create(MB);
        if (!instance) {
            std::cerr << "Failed to initialize VmReader with buffer size " << instance->sz << std::endl;
            return nullptr;
        }
    }

    return instance;
}

ssize_t
VmReader::safe_copy(pid_t process_id,
                    const struct iovec* local_iov,
                    unsigned long liovcnt,
                    const struct iovec* remote_iov,
                    unsigned long riovcnt,
                    unsigned long flags)
{
    (void)process_id;
    (void)flags;
    if (liovcnt != 1 || riovcnt != 1) {
        // Unsupported
        return 0;
    }

    // Check to see if we need to resize the buffer
    if (remote_iov[0].iov_len > sz) {
        if (ftruncate(fd, remote_iov[0].iov_len) == -1) {
            return 0;
        } else {
            void* tmp = mremap(buffer, sz, remote_iov[0].iov_len, MREMAP_MAYMOVE);
            if (tmp == MAP_FAILED) {
                return 0;
            }
            buffer = tmp; // no need to munmap
            sz = remote_iov[0].iov_len;
        }
    }

    ssize_t ret = pwritev(fd, remote_iov, riovcnt, 0);
    if (ret == -1) {
        return ret;
    }

    // Copy the data from the buffer to the remote process
    std::memcpy(local_iov[0].iov_base, buffer, local_iov[0].iov_len);
    return ret;
}

bool
read_process_vm_init()
{
    VmReader* _ = VmReader::get_instance();
    return !!_;
}

ssize_t
vmreader_safe_copy(pid_t process_id,
                   const struct iovec* local_iov,
                   unsigned long liovcnt,
                   const struct iovec* remote_iov,
                   unsigned long riovcnt,
                   unsigned long flags)
{
    auto reader = VmReader::get_instance();
    if (!reader)
        return 0;
    return reader->safe_copy(process_id, local_iov, liovcnt, remote_iov, riovcnt, flags);
}

__attribute__((constructor)) void
init_safe_copy()
{
    if (use_alternative_copy_memory()) {
        if (init_segv_catcher() == 0) {
            safe_copy = safe_memcpy_wrapper;
            return;
        }

        std::cerr << "Failed to initialize segv catcher. Using process_vm_readv instead." << std::endl;
    }

    char src[128];
    char dst[128];
    for (size_t i = 0; i < 128; i++) {
        src[i] = 0x41;
        dst[i] = ~0x42;
    }

    // Check to see that process_vm_readv works, unless it's overridden
    const char force_override_str[] = "ECHION_ALT_VM_READ_FORCE";
    const char* force_override = std::getenv(force_override_str);
    if (!force_override || !is_truthy(force_override)) {
        struct iovec iov_dst = { dst, sizeof(dst) };
        struct iovec iov_src = { src, sizeof(src) };
        ssize_t result = process_vm_readv(getpid(), &iov_dst, 1, &iov_src, 1, 0);

        // If we succeed, then use process_vm_readv
        if (result == sizeof(src)) {
            safe_copy = process_vm_readv;
            return;
        }
    }

    // Else, we have to setup the writev method
    if (!read_process_vm_init()) {
        // std::cerr might not have been fully initialized at this point, so use
        // fprintf instead.
        fprintf(stderr, "Failed to initialize all safe copy interfaces\n");
        failed_safe_copy = true;
        return;
    }

    safe_copy = vmreader_safe_copy;
}
#endif // PL_LINUX

int
copy_memory(proc_ref_t proc_ref, const void* addr, ssize_t len, void* buf)
{
    ssize_t result = -1;

    // Early exit on zero length
    if (len <= 0) {
        return 0;
    }

    // Early exit on zero page
    if (reinterpret_cast<uintptr_t>(addr) < 4096) {
        return result;
    }

#if defined PL_LINUX
    struct iovec local[1];
    struct iovec remote[1];

    local[0].iov_base = buf;
    local[0].iov_len = len;
    remote[0].iov_base = const_cast<void*>(addr);
    remote[0].iov_len = len;

    result = safe_copy(proc_ref, local, 1, remote, 1, 0);

#elif defined PL_DARWIN
    kern_return_t kr = safe_copy(proc_ref,
                                 reinterpret_cast<mach_vm_address_t>(addr),
                                 len,
                                 reinterpret_cast<mach_vm_address_t>(buf),
                                 reinterpret_cast<mach_vm_size_t*>(&result));

    if (kr != KERN_SUCCESS)
        return -1;

#endif

    return len != result;
}

void
_set_pid(pid_t _pid)
{
    pid = _pid;
}
