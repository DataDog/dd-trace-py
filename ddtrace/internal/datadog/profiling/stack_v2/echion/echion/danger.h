#pragma once

#include <cassert>
#include <csignal>
#include <cstddef>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <sys/uio.h>
#include <unistd.h>

#if defined PL_DARWIN
#include <pthread.h>

#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <mach/machine/kern_return.h>
#include <sys/sysctl.h>
#include <sys/types.h>
#endif

int
init_segv_catcher();

#if defined PL_LINUX
ssize_t
safe_memcpy_wrapper(pid_t,
                    const struct iovec* __dstvec,
                    unsigned long int __dstiovcnt,
                    const struct iovec* __srcvec,
                    unsigned long int __srciovcnt,
                    unsigned long int);
#elif defined PL_DARWIN
kern_return_t
safe_memcpy_wrapper(vm_map_read_t target_task,
                    mach_vm_address_t address,
                    mach_vm_size_t size,
                    mach_vm_address_t data,
                    mach_vm_size_t* outsize);
#endif

struct ThreadAltStack
{
  private:
    inline static constexpr size_t kAltStackSize = 1 << 20; // 1 MiB

  public:
    void* mem = nullptr;
    size_t size = 0;
    bool ready = false;

    int ensure_installed()
    {
        if (ready) {
            return 0;
        }

        // If an altstack is already present, keep it.
        stack_t cur{};
        if (sigaltstack(nullptr, &cur) == 0 && !(cur.ss_flags & SS_DISABLE)) {
            ready = true;
            return 0;
        }

        void* stack_mem = mmap(nullptr, kAltStackSize, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (stack_mem == MAP_FAILED) {
            std::cerr << "Failed to allocate alt stack. Memory copying may not work. "
                      << "Error: " << strerror(errno) << std::endl;
            return -1;
        }

        stack_t ss{};
        ss.ss_sp = stack_mem;
        ss.ss_size = kAltStackSize;
        ss.ss_flags = 0;
        if (sigaltstack(&ss, nullptr) != 0) {
            std::cerr << "Failed to set alt stack. Memory copying may not work. "
                      << "Error: " << strerror(errno) << std::endl;
            return -1;
        }

        this->mem = stack_mem;
        this->size = kAltStackSize;
        this->ready = true;

        return 0;
    }

    ~ThreadAltStack()
    {
        if (!ready) {
            return;
        }

        // Optional cleanup: disable and free. Safe at thread exit.
        stack_t disable{};
        disable.ss_flags = SS_DISABLE;
        (void)sigaltstack(&disable, nullptr);
        munmap(mem, size);
    }
};
