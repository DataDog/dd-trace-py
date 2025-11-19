#pragma once

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
extern MEMALLOC_TLS bool _MEMALLOC_ON_THREAD;

/* RAII guard for reentrancy protection. Automatically acquires the guard in the
 * constructor and releases it in the destructor.
 *
 * Ordinarily, a process-wide semaphore would require a CAS, but since this is
 * thread-local we can just set it.  */
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
