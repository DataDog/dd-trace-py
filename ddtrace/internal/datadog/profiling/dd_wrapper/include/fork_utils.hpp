#pragma once

#include <new>

// TSan doesn't see placement-new as a mutex reset (libstdc++ std::mutex()
// doesn't call pthread_mutex_init). Tell TSan explicitly.
#if defined(__SANITIZE_THREAD__) || (defined(__has_feature) && __has_feature(thread_sanitizer))
#define DD_TSAN_ENABLED 1
extern "C" {
void __tsan_mutex_destroy(void* addr, unsigned flags);
void __tsan_mutex_create(void* addr, unsigned flags);
}
#else
#define DD_TSAN_ENABLED 0
#endif

namespace Datadog {

// Reset a mutex after fork via placement-new, with TSan annotations so the
// sanitizer sees the old mutex as destroyed and the new one as fresh.
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
