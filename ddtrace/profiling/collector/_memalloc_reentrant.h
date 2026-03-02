#pragma once

#include <stdint.h>

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

/* Tracks which allocator hook operation is currently in progress on this thread.
 * Used to prevent reentrant heap tracking (which would corrupt the heap tracker's
 * data structures) and to detect reentrant calls in assert builds. */
enum memalloc_op_t : uint8_t
{
    MEMALLOC_OP_NONE = 0,
    MEMALLOC_OP_MALLOC = 1,
    MEMALLOC_OP_FREE = 2,
};

extern MEMALLOC_TLS memalloc_op_t _MEMALLOC_CURRENT_OP;

#ifdef MEMALLOC_ASSERT_ON_REENTRY
#include <stdio.h>
#include <stdlib.h>

/* Print a diagnostic to stderr and abort. We're about to abort() anyway,
 * so even if fprintf triggers another reentrant call, the worst case is
 * a double-abort — not a hang or data corruption. */
static inline void
_memalloc_abort_reentry(const char* inner_op)
{
    const char* outer_op = "unknown";
    switch (_MEMALLOC_CURRENT_OP) {
        case MEMALLOC_OP_MALLOC:
            outer_op = "malloc";
            break;
        case MEMALLOC_OP_FREE:
            outer_op = "free";
            break;
        default:
            outer_op = "none";
            break;
    }

    fprintf(stderr, "[memalloc] FATAL: reentrant allocator hook detected: %s -> %s\n", outer_op, inner_op);
    abort();
}
#endif /* MEMALLOC_ASSERT_ON_REENTRY */

/* RAII guard for reentrancy protection. Sets _MEMALLOC_CURRENT_OP in the
 * constructor and clears it in the destructor.
 *
 * If the TLS is already set (reentrant call), the guard does not acquire:
 *  - In assert builds, it aborts immediately for early detection.
 *  - In production builds, callers check acquired() / operator bool() to
 *    skip heap tracking and avoid data structure corruption.
 *
 * Since this is thread-local, no CAS or atomic is needed.  */
class memalloc_reentrant_guard_t
{
  public:
    explicit memalloc_reentrant_guard_t(memalloc_op_t op)
      : acquired_(false)
      , op_(op)
    {
        if (_MEMALLOC_CURRENT_OP == MEMALLOC_OP_NONE) {
            _MEMALLOC_CURRENT_OP = op;
            acquired_ = true;
        } else {
#ifdef MEMALLOC_ASSERT_ON_REENTRY
            const char* inner = (op == MEMALLOC_OP_MALLOC) ? "malloc" : "free";
            _memalloc_abort_reentry(inner);
#endif
        }
    }

    ~memalloc_reentrant_guard_t()
    {
        if (acquired_) {
            _MEMALLOC_CURRENT_OP = MEMALLOC_OP_NONE;
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
    memalloc_op_t op_;
};
