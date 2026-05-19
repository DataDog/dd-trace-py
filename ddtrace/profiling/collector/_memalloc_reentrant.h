#pragma once

#include <cstdlib>
#include <unistd.h>

// Thread-local storage macro for Unix (GCC/Clang)
// NB - we explicitly specify global-dynamic on Unix because the others are problematic.
// See e.g. https://fuchsia.dev/fuchsia-src/development/kernel/threads/tls for
// an explanation of thread-local storage access models. global-dynamic is the
// most general access model and is always correct to use, if not always the
// fastest, for a library like this which will be dlopened by an executable.
// The C toolchain should do the right thing without this attribute if it
// sees we're building a shared library. But we've been bit by issues related
// to this before, and it doesn't hurt to explicitly declare the model here.
#define MEMALLOC_TLS __attribute__((tls_model("global-dynamic"))) __thread

/* True while the malloc allocator hook is running on this thread.
 * Used to prevent reentrant heap tracking (which would corrupt the heap
 * tracker's data structures) and to detect reentrant calls in assert builds. */
extern MEMALLOC_TLS bool _MEMALLOC_ON_THREAD;

#ifdef MEMALLOC_ASSERT_ON_REENTRY
static inline void
_memalloc_abort_malloc_reentry(void)
{
    static constexpr char msg[] = "[memalloc] FATAL: reentrant allocator hook detected: malloc -> malloc\n";
    (void)!write(STDERR_FILENO, msg, sizeof(msg) - 1);
    std::abort();
}

static inline void
_memalloc_abort_free_reentry(void)
{
    static constexpr char msg[] = "[memalloc] FATAL: reentrant allocator hook detected: malloc -> free\n";
    (void)!write(STDERR_FILENO, msg, sizeof(msg) - 1);
    std::abort();
}
#endif // MEMALLOC_ASSERT_ON_REENTRY

/* RAII guard for reentrancy protection. Sets _MEMALLOC_ON_THREAD in the
 * constructor and clears it in the destructor.
 *
 * If _MEMALLOC_ON_THREAD is already set (reentrant call), the guard does
 * not acquire:
 *  - In assert builds, it aborts immediately for early detection.
 *  - In production builds, callers check acquired() / operator bool() to
 *    skip heap tracking and avoid data structure corruption.
 *
 * Since this is thread-local, no CAS or atomic is needed.  */
class memalloc_reentrant_guard_t
{
  public:
    memalloc_reentrant_guard_t()
      : acquired_(false)
    {
        if (!_MEMALLOC_ON_THREAD) {
            _MEMALLOC_ON_THREAD = true;
            acquired_ = true;
        }
#ifdef MEMALLOC_ASSERT_ON_REENTRY
        else {
            _memalloc_abort_malloc_reentry();
        }
#endif // MEMALLOC_ASSERT_ON_REENTRY
    }

    ~memalloc_reentrant_guard_t()
    {
        /* We only release _MEMALLOC_ON_THREAD if this guard object successfully
         * acquired it (acquired_ == true). This is important because if acquisition failed
         * (we're already in a reentrant call), we don't own the lock and shouldn't release it. */
        if (acquired_) {
            _MEMALLOC_ON_THREAD = false;
        }
    }

    // Non-copyable, non-movable
    memalloc_reentrant_guard_t(const memalloc_reentrant_guard_t&) = delete;
    memalloc_reentrant_guard_t& operator=(const memalloc_reentrant_guard_t&) = delete;
    memalloc_reentrant_guard_t(memalloc_reentrant_guard_t&&) = delete;
    memalloc_reentrant_guard_t& operator=(memalloc_reentrant_guard_t&&) = delete;

    /* Check if the guard was successfully acquired */
    bool acquired() const { return acquired_; }

    /* Implicit conversion to bool for easy checking */
    operator bool() const { return acquired_; }

  private:
    bool acquired_;
};
