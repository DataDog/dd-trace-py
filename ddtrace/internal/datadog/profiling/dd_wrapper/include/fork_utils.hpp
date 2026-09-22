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

// Reset a mutex after fork via placement-new with TSan annotations.
// Used for mutexes that are NOT locked in prefork() — if the mutex was
// locked in prefork(), unlock it in postfork_child() instead.
//
// TSan annotations tell the sanitizer the old mutex is gone and a new one
// exists at the same address. If the mutex happens to be locked by a
// now-dead thread when fork occurs, TSan may still warn; the proper fix
// is to lock the mutex in prefork() and unlock in postfork_child().
template<typename Mutex>
inline void
reset_mutex_after_fork(Mutex& mtx)
{
#if DD_TSAN_ENABLED
    __tsan_mutex_destroy(&mtx, 0);
#endif
    new (&mtx) Mutex();
#if DD_TSAN_ENABLED
    __tsan_mutex_create(&mtx, 0);
#endif
}

} // namespace Datadog
