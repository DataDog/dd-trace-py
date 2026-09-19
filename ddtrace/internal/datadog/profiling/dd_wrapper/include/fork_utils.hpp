#pragma once

#include <new>

// Detect TSan. GCC uses __SANITIZE_THREAD__, Clang uses __has_feature.
// __has_feature must be tested in a separate #if because GCC doesn't
// define the macro and the preprocessor rejects __has_feature(x) as
// a syntax error rather than evaluating it to 0.
#if defined(__SANITIZE_THREAD__)
#define DD_TSAN_ENABLED 1
#elif defined(__has_feature)
#if __has_feature(thread_sanitizer)
#define DD_TSAN_ENABLED 1
#endif
#endif

#ifndef DD_TSAN_ENABLED
#define DD_TSAN_ENABLED 0
#endif

#if DD_TSAN_ENABLED
extern "C" {
void __tsan_mutex_destroy(void* addr, unsigned flags);
void __tsan_mutex_create(void* addr, unsigned flags);
}
#endif

namespace Datadog {

// Reset a mutex after fork via placement-new, with TSan annotations so the
// sanitizer sees the old mutex as destroyed and the new one as fresh.
// After fork(), the child inherits the parent's locked mutex state in TSan's
// shadow memory. The linker_init flag (1) tells TSan to skip the
// locked-mutex check on destroy — the lock is held by a now-dead parent
// thread, which is expected, not a bug.
template<typename Mutex>
inline void
reset_mutex_after_fork(Mutex& mtx)
{
#if DD_TSAN_ENABLED
    __tsan_mutex_destroy(&mtx, /*flags=*/1 /* __tsan_mutex_linker_init */);
#endif
    new (&mtx) Mutex();
#if DD_TSAN_ENABLED
    __tsan_mutex_create(&mtx, 0);
#endif
}

} // namespace Datadog
